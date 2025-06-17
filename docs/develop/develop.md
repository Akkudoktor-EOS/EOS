% SPDX-License-Identifier: Apache-2.0
(develop-page)=

# Development Guide

## Development Prerequisites

Have or
[create](https://docs.github.com/en/get-started/start-your-journey/creating-an-account-on-github)
a [GitHub](https://github.com/) account.

Make shure all the source installation prequistes are installed. See the
[installation guideline](#install-page) for a detailed list of tools.

Under Linux the [make](https://www.gnu.org/software/make/manual/make.html) tool should be installed
as we have a lot of pre-fabricated commands for it.

Install your favorite editor or integrated development environment (IDE):

- Full-Featured IDEs

  - [Eclipse + PyDev](https://www.pydev.org/)
  - [KDevelop](https://www.kdevelop.org/)
  - [PyCharm](https://www.jetbrains.com/pycharm/)
  - ...

- Code Editors with Python Support

  - [Visual Studio Code (VS Code)](https://code.visualstudio.com/)
  - [Sublime Text](https://www.sublimetext.com/)
  - [Atom / Pulsar](https://pulsar-edit.dev/)
  - ...

- Python-Focused or Beginner-Friendly IDEs

  - [Spyder](https://www.spyder-ide.org/)
  - [Thonny](https://thonny.org/)
  - [IDLE](https://www.python.org/downloads/)
  - ...

## Step 1 – Fork the Repository

[Fork the EOS repository](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/working-with-forks/fork-a-repo)
to your GitHub account.

Clone your fork locally and add the EOS upstream remote to track updates.

```{eval-rst}
.. tabs::

  .. tab:: Windows

     .. code-block:: powershell

        git clone https://github.com/<YOURUSERNAME>/EOS.git
        cd EOS
        git remote add eos https://github.com/Akkudoktor-EOS/EOS.git

  .. tab:: Linux

     .. code-block:: bash

        git clone https://github.com/<YOURUSERNAME>/EOS.git
        cd EOS
        git remote add eos https://github.com/Akkudoktor-EOS/EOS.git
```

Replace `<YOURUSERNAME>` with your GitHub username.

## Step 2 – Development Setup

This is recommended for developers who want to modify the source code and test changes locally.

### Step 2.1 – Create a Virtual Environment

```{eval-rst}
.. tabs::

  .. tab:: Windows

     .. code-block:: powershell

        python -m venv .venv
        .venv\Scripts\pip install --upgrade pip
        .venv\Scripts\pip install -r requirements-dev.txt
        .venv\Scripts\pip install build
        .venv\Scripts\pip install -e .

  .. tab:: Linux

     .. code-block:: bash

        python3 -m venv .venv
        .venv/bin/pip install --upgrade pip
        .venv/bin/pip install -r requirements-dev.txt
        .venv/bin/pip install build
        .venv/bin/pip install -e .

  .. tab:: Linux Make

     .. code-block:: bash

        make install
```

### Step 2.2 – Activate the Virtual Environment

```{eval-rst}
.. tabs::

  .. tab:: Windows

     .. code-block:: powershell

        .venv\Scripts\activate.bat

  .. tab:: Linux

     .. code-block:: bash

        source .venv/bin/activate
```

### Step 2.3 - Install pre-commit

Our code style and commit message checks use [`pre-commit`](https://pre-commit.com).

```{eval-rst}
.. tabs::

  .. tab:: Windows

     .. code-block:: powershell

        pre-commit install
        pre-commit install --hook-type commit-msg --hook-type pre-push

  .. tab:: Linux

     .. code-block:: bash

        pre-commit install
        pre-commit install --hook-type commit-msg --hook-type pre-push
```

## Step 3 - Run EOS

Make EOS accessible at [http://localhost:8503/docs](http://localhost:8503/docs) and EOSdash at
[http://localhost:8504](http://localhost:8504).

### Option 1 – Using Python Virtual Environment

```{eval-rst}
.. tabs::

  .. tab:: Windows

     .. code-block:: powershell

        python -m akkudoktoreos.server.eos

  .. tab:: Linux

     .. code-block:: bash

        python -m akkudoktoreos.server.eos

  .. tab:: Linux Make

     .. code-block:: bash

        make run
```

To have full control of the servers during development you may start the servers independently -
e.g. in different terminal windows. Don't forget to activate the virtual environment in your
terminal window.

:::{admonition} Note
:class: note
If you killed or stopped the servers shortly before, the ports may still be occupied by the last
processes. It may take more than 60 seconds until the ports are released.
:::

You may add the `--reload true` parameter to have the servers automatically restarted on source code
changes. It is best to also add `--startup_eosdash false` to EOS to prevent the automatic restart
interfere with the EOS server trying to start EOSdash.

<!-- pyml disable line-length -->
```{eval-rst}
.. tabs::

  .. tab:: Windows

     .. code-block:: powershell

        python -m akkudoktoreos.server.eosdash --host localhost --port 8504 --log_level DEBUG --reload true

  .. tab:: Linux

     .. code-block:: bash

        python -m akkudoktoreos.server.eosdash --host localhost --port 8504 --log_level DEBUG --reload true

  .. tab:: Linux Make

     .. code-block:: bash

        make run-dash-dev
```

```{eval-rst}
.. tabs::

  .. tab:: Windows

     .. code-block:: powershell

        python -m akkudoktoreos.server.eos --host localhost --port 8503 --log_level DEBUG --startup_eosdash false --reload true

  .. tab:: Linux

     .. code-block:: bash

        python -m akkudoktoreos.server.eos --host localhost --port 8503 --log_level DEBUG --startup_eosdash false --reload true

  .. tab:: Linux Make

     .. code-block:: bash

        make run-dev
```
<!-- pyml enable line-length -->

### Option 2 – Using Docker

#### Step 3.1 – Build the Docker Image

```{eval-rst}
.. tabs::

  .. tab:: Windows

     .. code-block:: powershell

        docker build -t akkudoktoreos .

  .. tab:: Linux

     .. code-block:: bash

        docker build -t akkudoktoreos .
```

#### Step 3.2 – Run the Container

```{eval-rst}
.. tabs::

  .. tab:: Windows

     .. code-block:: powershell

        docker run -d `
          --name akkudoktoreos `
          -p 8503:8503 `
          -p 8504:8504 `
          -e OPENBLAS_NUM_THREADS=1 `
          -e OMP_NUM_THREADS=1 `
          -e MKL_NUM_THREADS=1 `
          -e EOS_SERVER__HOST=0.0.0.0 `
          -e EOS_SERVER__PORT=8503 `
          -e EOS_SERVER__EOSDASH_HOST=0.0.0.0 `
          -e EOS_SERVER__EOSDASH_PORT=8504 `
          --ulimit nproc=65535:65535 `
          --ulimit nofile=65535:65535 `
          --security-opt seccomp=unconfined `
          akkudoktor-eos:latest

  .. tab:: Linux

     .. code-block:: bash

          docker run -d \
            --name akkudoktoreos \
            -p 8503:8503 \
            -p 8504:8504 \
            -e OPENBLAS_NUM_THREADS=1 \
            -e OMP_NUM_THREADS=1 \
            -e MKL_NUM_THREADS=1 \
            -e EOS_SERVER__HOST=0.0.0.0 \
            -e EOS_SERVER__PORT=8503 \
            -e EOS_SERVER__EOSDASH_HOST=0.0.0.0 \
            -e EOS_SERVER__EOSDASH_PORT=8504 \
            --ulimit nproc=65535:65535 \
            --ulimit nofile=65535:65535 \
            --security-opt seccomp=unconfined \
            akkudoktor-eos:latest
```

#### Step 3.3 – Manage the Container

```{eval-rst}
.. tabs::

  .. tab:: Windows

     .. code-block:: powershell

        docker logs -f akkudoktoreos
        docker stop akkudoktoreos
        docker start akkudoktoreos
        docker rm -f akkudoktoreos

  .. tab:: Linux

     .. code-block:: bash

        docker logs -f akkudoktoreos
        docker stop akkudoktoreos
        docker start akkudoktoreos
        docker rm -f akkudoktoreos
```

For detailed Docker instructions, refer to
**[Method 3 & 4: Installation with Docker](install.md#method-3-installation-with-docker-dockerhub)**
and
**[Method 4: Docker Compose](install.md#method-4-installation-with-docker-docker-compose)**.

### Step 4 - Create the changes

#### Step 4.1 - Create a development branch

```bash
git checkout -b <MY_DEVELOPMENT_BRANCH>
```

Replace `<MY_DEVELOPMENT_BRANCH>` with the development branch name. The branch name shall be of the
format (feat|fix|chore|docs|refactor|test)/[a-z0-9._-]+, e.g:

- feat/my_cool_new_feature
- fix/this_annoying_bug
- ...

#### Step 4.2 – Edit the sources

Use your fovourite editor or IDE to edit the sources.

#### Step 4.3 - Check the source code for correct format

```{eval-rst}
.. tabs::

  .. tab:: Windows

     .. code-block:: powershell

        pre-commit run --all-files

  .. tab:: Linux

     .. code-block:: bash

        pre-commit run --all-files

  .. tab:: Linux Make

     .. code-block:: bash

        make format
```

#### Step 4.4 - Test the changes

At a minimum, you should run the module tests:

```{eval-rst}
.. tabs::

  .. tab:: Windows

     .. code-block:: powershell

        pytest -vs --cov src --cov-report term-missing

  .. tab:: Linux

     .. code-block:: bash

        pytest -vs --cov src --cov-report term-missing

  .. tab:: Linux Make

     .. code-block:: bash

        make test
```

You should also run the system tests. These include additional tests that interact with real
resources:

```{eval-rst}
.. tabs::

  .. tab:: Windows

     .. code-block:: powershell

        pytest --system-test -vs --cov src --cov-report term-missing

  .. tab:: Linux

     .. code-block:: bash

        pytest --system-test -vs --cov src --cov-report term-missing

  .. tab:: Linux Make

     .. code-block:: bash

        make test-system
```

#### Step 4.5 - Commit the changes

Add the changed and new files to the commit.

Create a commit.

### Step 5 - Pull request

Before creating a pull request assure the changes are based on the latest EOS upstream.

Update your local main branch:

```bash
git checkout main
git pull eos main
```

Switch back to your local development branch and rebase to main.

```bash
git checkout <MY_DEVELOPMENT_BRANCH>
git rebase -i main
```

During rebase you can also squash your changes into one (preferred) or a set of commits that have
proper commit messages and can easily be reviewed.

After rebase run the tests once again.

If everything is ok push the commit(s) to your fork on Github.

```bash
git push -f origin
```

If your push by intention does not comply to the rules you can skip the verification by:

```bash
git push -f --no-verify origin
```

<!-- pyml disable line-length -->
Once ready, [submit a pull request](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/proposing-changes-to-your-work-with-pull-requests/creating-a-pull-request-from-a-fork)
with your fork to the [Akkudoktor-EOS/EOS@master](https://github.com/Akkudoktor-EOS/EOS) repository.
<!-- pyml enable line-length -->

## Developer Tips

### Keep Your Fork Updated

Regularly pull changes from the eos repository to avoid merge conflicts:

```{eval-rst}
.. tabs::

  .. tab:: Windows

     .. code-block:: powershell

        git checkout main
        git pull eos main
        git push origin

  .. tab:: Linux

     .. code-block:: bash

        git checkout main
        git pull eos main
        git push origin
```

Rebase your development branch to the latest eos main branch.

```{eval-rst}
.. tabs::

  .. tab:: Windows

     .. code-block:: powershell

        git checkout  <MY_DEVELOPMENT_BRANCH>
        git rebase -i main

  .. tab:: Linux

     .. code-block:: bash

        git checkout  <MY_DEVELOPMENT_BRANCH>
        git rebase -i main
```

### Create Feature Branches

Work in separate branches for each feature or bug fix:

```{eval-rst}
.. tabs::

  .. tab:: Windows

     .. code-block:: powershell

        git checkout -b feat/my-feature

  .. tab:: Linux

     .. code-block:: bash

        git checkout -b feat/my-feature
```

### Run Tests Frequently

Ensure your changes do not break existing functionality:

```{eval-rst}
.. tabs::

  .. tab:: Windows

     .. code-block:: powershell

        pytest -vs --cov src --cov-report term-missing

  .. tab:: Linux

     .. code-block:: bash

        pytest -vs --cov src --cov-report term-missing

  .. tab:: Linux Make

     .. code-block:: bash

        make test
```

### Follow Coding Standards

Keep your code consistent with existing style and conventions.

### Use Issues for Discussion

Before making major changes, open an issue or discuss with maintainers.

### Document Changes

Update docstrings, comments, and any relevant documentation.
