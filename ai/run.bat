@echo off
setlocal EnableExtensions EnableDelayedExpansion

:: ==========================================================================
:: ai\run.bat
:: Entry point for the AkkudoktorEOS AI development sandbox (Windows CMD).
::
:: Launcher:
::   - Sanity-checks ai\compose.yaml for the main sandbox controls. This is not
::     a complete validation; the protection of the sandbox definition is the
::     read-only mount of ai/ in the container.
::   - Only forwards arguments after `--` as the in-container command, never
::     as docker compose options. Each argument is forwarded with its own
::     quotes, so cmd does not interpret |, &, < or > inside a quoted
::     argument. `!` and `%` cannot be passed through; use run.ps1 for them.
::   - Optional write access to ai/skills (AI_SANDBOX_SKILLS_RW=1).
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
:: 0. Arguments. Docker compose options cannot be passed; everything after
::    `--` is the command to run in the container.
:: ==========================================================================
set "EXTRA_CMD="
set "SAW_DASHDASH=0"

:parse_args
if "%~1"=="" goto end_parse_args

:: Everything after `--` is forwarded to the container as given. %1 keeps
:: the quotes of the argument, which protect |, &, < and > inside it from
:: cmd; "%~1" inside quotes would expose them to cmd.
if "%SAW_DASHDASH%"=="1" (
    rem Forward the argument with its own quotes.
    if defined EXTRA_CMD (
        set EXTRA_CMD=!EXTRA_CMD! %1
    ) else (
        set EXTRA_CMD=%1
    )
    shift
    goto parse_args
)

if "%~1"=="--" (
    set "SAW_DASHDASH=1"
    shift
    goto parse_args
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
:: 2. Sanity-check the sandbox controls in compose.yaml.
::    This catches accidental edits, not every weakening edit. The agent
::    cannot edit compose.yaml from inside the sandbox (ai/ is read-only
::    there); review changes to ai/ before you start the sandbox.
::    CMD cannot span lines, so multi-line YAML values are checked
::    line-by-line with findstr. Patterns must not use `$`: findstr only
::    matches it before CRLF, and the file may have LF line endings.
:: ==========================================================================
echo ==^> Checking sandbox controls in %COMPOSE_FILE% ...

call :require_line "read_only:[ ]*true"                  "Missing read_only:true."
if errorlevel 1 goto verify_failed

call :require_line "no-new-privileges:[ ]*true"          "Missing no-new-privileges:true."
if errorlevel 1 goto verify_failed

:: cap_drop must be present and must include ALL.
call :require_line "cap_drop:"                            "Missing cap_drop."
if errorlevel 1 goto verify_failed
:: No caret anchor: call doubles carets in quoted arguments.
call :require_line "[ ]-[ ]*ALL"                          "cap_drop must include ALL."
if errorlevel 1 goto verify_failed

:: ai/ must be mounted read-only.
call :require_text "../ai:/workspace/ai:ro"               "ai/ must be mounted read-only."
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
:: 5. Port publishing is opt-in. `docker compose run` publishes the ports of
::    compose.yaml (loopback-only, verified above) only with --service-ports.
::    Off by default, so several sandboxes can run in parallel.
:: ==========================================================================
set "PORTS_FLAG="
if /I "%AI_SANDBOX_PORTS%"=="1" (
    echo ==^> Publishing the compose ports on 127.0.0.1 ^(AI_SANDBOX_PORTS=1^)
    set "PORTS_FLAG=--service-ports"
) else (
    echo ==^> Ports not published ^(set AI_SANDBOX_PORTS=1 to publish them^)
)

:: ==========================================================================
:: 6. ai/skills is read-only in the container unless AI_SANDBOX_SKILLS_RW=1.
::    Always set the mode explicitly, so a value in ai\.env cannot change it.
::    setlocal keeps the variable out of the calling CMD session.
:: ==========================================================================
if "%AI_SANDBOX_SKILLS_RW%"=="1" (
    echo ==^> ai/skills is writable in the sandbox ^(AI_SANDBOX_SKILLS_RW=1^)
    echo     Review every change to ai/skills before you commit or push it.
    set "AI_SANDBOX_SKILLS_MODE=rw"
) else (
    set "AI_SANDBOX_SKILLS_MODE=ro"
)

:: ==========================================================================
:: 7. Run. The forwarded command runs in the Linux container as given;
::    each argument in EXTRA_CMD keeps its own quotes.
:: ==========================================================================
echo ==^> Starting AI dev sandbox...

call docker compose -f "%COMPOSE_FILE%" run --rm %BUILD_FLAG% %PORTS_FLAG% ai-dev %EXTRA_CMD%
set "RC=%ERRORLEVEL%"

popd >nul
exit /b %RC%

:: ==========================================================================
:: Subroutines
:: ==========================================================================

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

:: :require_text <literal> <message>
:: Returns errorlevel 1 if the literal text is not found in %COMPOSE_FILE%.
:require_text
findstr /L /C:"%~1" "%COMPOSE_FILE%" >nul 2>&1
if errorlevel 1 (
    echo ERROR: compose.yaml does not contain: %~1 1>&2
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
                rem strip all double and single quotes
                set "ENTRY=!ENTRY:"=!"
                set "ENTRY=!ENTRY:'=!"
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
