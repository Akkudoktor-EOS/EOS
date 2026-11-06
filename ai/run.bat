@echo off
setlocal EnableExtensions EnableDelayedExpansion

:: ==========================================================================
:: ai\run.bat
:: Entry point for the AkkudoktorEOS AI development sandbox (Windows CMD).
::
:: Hardened launcher:
::   - Fails closed if compose.yaml is missing any sandbox control.
::   - Rejects command-line flags that would weaken isolation
::     (--privileged, --cap-add, --device, --pid=host,
::      -v /var/run/docker.sock:..., -v //./pipe/docker_engine:...).
::   - Only forwards arguments after `--` as the in-container command.
::   - Requires every published port to bind to 127.0.0.1 (loopback on the host).
::   - Optional rebuild (AI_SANDBOX_REBUILD=1); cached image otherwise.
::   - Windows: UID/GID bind-mount mapping does not apply, HOST_UID/HOST_GID
::     fall back to the defaults from ai\.env.
:: ==========================================================================

:: Resolve script directory and repo root.
set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
for %%I in ("%SCRIPT_DIR%\..") do set "REPO_ROOT=%%~fI"

pushd "%REPO_ROOT%" >nul 2>&1
if errorlevel 1 (
    echo ERROR: cannot cd into "%REPO_ROOT%". 1>&2
    exit /b 1
)

set "COMPOSE_FILE=ai\compose.yaml"
set "ENV_FILE=ai\.env"
set "ENV_EXAMPLE=ai\.env.example"

:: ==========================================================================
:: 0. Reject CLI flags that weaken isolation.
::    Forwarded *container* commands must come after `--`.
:: ==========================================================================
set "EXTRA_CMD="
set "SAW_DASHDASH=0"

:parse_args
if "%~1"=="" goto end_parse_args

if "%SAW_DASHDASH%"=="1" (
    :: Everything after `--` is forwarded to the container verbatim.
    if defined EXTRA_CMD (
        set "EXTRA_CMD=!EXTRA_CMD! "%~1""
    ) else (
        set "EXTRA_CMD="%~1""
    )
    shift
    goto parse_args
)

if "%~1"=="--" (
    set "SAW_DASHDASH=1"
    shift
    goto parse_args
)

call :check_forbidden "%~1"
if errorlevel 1 (
    echo ERROR: refusing to pass '%~1' to docker compose run. 1>&2
    echo        This flag can weaken the sandbox and is not allowed. 1>&2
    popd >nul
    exit /b 2
)

echo ERROR: extra arguments must be supplied after '--' as the in-container command. 1>&2
echo        Example: ai\run.bat -- bash -lc "pytest -q" 1>&2
popd >nul
exit /b 2

:end_parse_args

:: ==========================================================================
:: 1. Prerequisites.
:: ==========================================================================
echo ==^> Checking prerequisites...

where docker >nul 2>&1
if errorlevel 1 (
    echo ERROR: docker is not installed. 1>&2
    popd >nul
    exit /b 1
)

docker compose version >nul 2>&1
if errorlevel 1 (
    echo ERROR: docker compose v2 is not available. 1>&2
    popd >nul
    exit /b 1
)

docker info >nul 2>&1
if errorlevel 1 (
    echo ERROR: docker daemon is not reachable. 1>&2
    echo        - Is Docker Desktop running? 1>&2
    echo        - Try 'docker info' manually. 1>&2
    popd >nul
    exit /b 1
)

if not exist "pyproject.toml" (
    echo ERROR: pyproject.toml not found; run from inside the AkkudoktorEOS repository. 1>&2
    popd >nul
    exit /b 1
)

if not exist "%COMPOSE_FILE%" (
    echo ERROR: %COMPOSE_FILE% missing. 1>&2
    popd >nul
    exit /b 1
)

:: ==========================================================================
:: 2. Verify sandbox controls in compose.yaml. Fails closed.
::    CMD cannot span lines, so multi-line YAML values are checked
::    line-by-line with anchored findstr patterns.
:: ==========================================================================
echo ==^> Verifying sandbox hardening in %COMPOSE_FILE% ...

call :require_line "read_only:[ ]*true"                  "Missing read_only:true."
if errorlevel 1 goto verify_failed

call :require_line "no-new-privileges:[ ]*true"          "Missing no-new-privileges:true."
if errorlevel 1 goto verify_failed

:: cap_drop must be present and must include ALL.
call :require_line "cap_drop:"                            "Missing cap_drop."
if errorlevel 1 goto verify_failed
call :require_line "[ ]-[ ]*ALL[ ]*$"                    "cap_drop must include ALL."
if errorlevel 1 goto verify_failed

:: Docker socket / named pipe must never be mounted.
findstr /R /C:"/var/run/docker\.sock" "%COMPOSE_FILE%" >nul 2>&1
if not errorlevel 1 (
    echo ERROR: compose.yaml mounts the Docker socket. This is an escape vector. 1>&2
    goto verify_failed
)
findstr /R /C:"//\./pipe/docker_engine" "%COMPOSE_FILE%" >nul 2>&1
if not errorlevel 1 (
    echo ERROR: compose.yaml mounts the Docker named pipe. This is an escape vector. 1>&2
    goto verify_failed
)

:: privileged:true is forbidden.
findstr /R /C:"privileged:[ ]*true" "%COMPOSE_FILE%" >nul 2>&1
if not errorlevel 1 (
    echo ERROR: compose.yaml sets privileged:true; refusing to start. 1>&2
    goto verify_failed
)

:: network_mode: host is forbidden.
findstr /R /C:"network_mode:[ ]*host" "%COMPOSE_FILE%" >nul 2>&1
if not errorlevel 1 (
    echo ERROR: compose.yaml sets network_mode: host; refusing to start. 1>&2
    goto verify_failed
)

:: Every port entry under `ports:` must start with 127.0.0.1:
call :check_ports
if errorlevel 1 goto verify_failed

goto verify_ok

:verify_failed
popd >nul
exit /b 3

:verify_ok

:: ==========================================================================
:: 3. Ensure ai\.env exists. HOST_UID/HOST_GID do not apply on Windows;
::    the defaults already present in ai\.env stand.
:: ==========================================================================
if not exist "%ENV_FILE%" (
    if exist "%ENV_EXAMPLE%" (
        echo ==^> Creating %ENV_FILE% from %ENV_EXAMPLE%
        copy /Y "%ENV_EXAMPLE%" "%ENV_FILE%" >nul
    ) else (
        echo ==^> Creating empty %ENV_FILE%
        type nul > "%ENV_FILE%"
    )
)

:: ==========================================================================
:: 4. Rebuild is opt-in.
:: ==========================================================================
set "BUILD_FLAG="
if /I "%AI_SANDBOX_REBUILD%"=="1" (
    echo ==^> Rebuilding image ^(AI_SANDBOX_REBUILD=1^)
    set "BUILD_FLAG=--build"
) else (
    echo ==^> Using cached image ^(set AI_SANDBOX_REBUILD=1 to rebuild^)
)

:: ==========================================================================
:: 5. Run.
:: ==========================================================================
echo ==^> Starting AI dev sandbox...

if defined BUILD_FLAG (
    if defined EXTRA_CMD (
        call docker compose -f "%COMPOSE_FILE%" run --rm %BUILD_FLAG% ai-dev cmd /c "%EXTRA_CMD%"
    ) else (
        call docker compose -f "%COMPOSE_FILE%" run --rm %BUILD_FLAG% ai-dev
    )
) else (
    if defined EXTRA_CMD (
        call docker compose -f "%COMPOSE_FILE%" run --rm ai-dev cmd /c "%EXTRA_CMD%"
    ) else (
        call docker compose -f "%COMPOSE_FILE%" run --rm ai-dev
    )
)
set "RC=%ERRORLEVEL%"

popd >nul
exit /b %RC%

:: ==========================================================================
:: Subroutines
:: ==========================================================================

:: :check_forbidden <arg>
:: Returns errorlevel 1 if <arg> is on the forbidden list, 0 otherwise.
:check_forbidden
setlocal EnableDelayedExpansion
set "A=%~1"
for %%F in (
    "--privileged"
    "--cap-add"
    "--device"
    "--security-opt"
    "--pid"
    "--ipc"
    "--network"
    "--net"
    "--userns"
    "--uts"
    "--volume"
    "-v"
    "--mount"
    "--volumes-from"
    "--group-add"
    "--runtime"
    "--publish"
    "-p"
) do (
    set "F=%%~F"
    if "!A!"=="!F!" endlocal & exit /b 1
    if "!A:~0,2!"=="!F!=" if "!A:~0,2!"=="-p" endlocal & exit /b 1
    :: Prefix match for `--flag=value` forms.
    if "!A:~0,10!"=="!F!=x_x_x_" endlocal & exit /b 1
    set "PREFIX=!F!="
    if "!A:~0,12!"=="!PREFIX!" endlocal & exit /b 1
    call set "PRE=%%A:0,!PRE_LEN!=%%"
)
endlocal & exit /b 0

:: :require_line <regex> <message>
:: Returns errorlevel 1 if the pattern is not found in %COMPOSE_FILE%.
:require_line
findstr /R /C:"%~1" "%COMPOSE_FILE%" >nul 2>&1
if errorlevel 1 (
    echo ERROR: compose.yaml does not contain a line matching: %~1 1>&2
    echo        %~2 1>&2
    exit /b 1
)
exit /b 0

:: :check_ports
:: Walks each line of compose.yaml. When inside the `ports:` block, every
:: `- "..."` entry must start with 127.0.0.1:.
:check_ports
setlocal EnableDelayedExpansion
set "IN_PORTS=0"
for /f "usebackq delims=" %%L in ("%COMPOSE_FILE%") do (
    set "LINE=%%L"
    set "STRIPPED=!LINE: =!"
    if "!STRIPPED!"=="ports:" (
        set "IN_PORTS=1"
    ) else (
        if "!IN_PORTS!"=="1" (
            if "!STRIPPED:~0,1!"=="-" (
                set "ENTRY=!STRIPPED:~1!"
                :: strip leading/trailing double quotes
                if "!ENTRY:~0,1!"=="""" set "ENTRY=!ENTRY:~1!"
                if "!ENTRY:~-1!"=="""" set "ENTRY=!ENTRY:~0,-1!"
                if "!ENTRY:~0,1!"=="'" set "ENTRY=!ENTRY:~1!"
                if "!ENTRY:~-1!"=="'" set "ENTRY=!ENTRY:~0,-1!"
                if "!ENTRY:~0,10!" NEQ "127.0.0.1:" (
                    echo ERROR: compose.yaml publishes a port on a non-loopback interface: !ENTRY! 1>&2
                    echo        Every entry under 'ports:' must start with '127.0.0.1:'. 1>&2
                    endlocal & exit /b 1
                )
            ) else if not "!STRIPPED!"=="" (
                set "IN_PORTS=0"
            )
        )
    )
)
endlocal & exit /b 0
