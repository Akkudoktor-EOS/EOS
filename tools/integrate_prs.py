"""Integrates multiple GitHub pull requests (PRs) into a specified integration branch.

This script automates the integration of multiple GitHub pull requests (PRs)
into a specified integration branch. It fetches each PR, creates a branch
from the PR, rebases the branch onto the integration branch, and then merges
it back into the integration branch. The process is logged to a branch-specific
log file, and if any step fails, the script exits and saves the current progress
(PR number and step) to a branch-specific JSON file. The script can be rerun,
and it will resume from where the last step failed. If the script is run again
on the same branch without providing PR numbers, it will reuse the PR numbers
from the first run.

Additionally, the script provides an option to delete all branches created
during the integration process by passing "delete" or "D" as an argument.

Steps:
    1. Fetch the pull request into a local branch.
    2. Create a branch from the fetched PR and rebase it onto the integration branch.
    3. Test the rebased branch.
    4. Merge the rebased branch into the integration branch.
    5. Optionally, delete all the branches created during the integration process.

If any step fails, the process will exit, and the failure point is logged
for resumption in the next run.

Usage:
    python integrate_prs.py <integration-branch> [<pr-number> ...]
    python integrate_prs.py <integration-branch> --delete

Example:
    python integrate_prs.py pr_integration 123 456 789
    python integrate_prs.py pr_integration --delete

Arguments:
    integration-branch: The target branch where PRs will be integrated.
    pr-number (optional): A list of pull request numbers to be fetched, rebased,
    tested and merged. If omitted and PRs were previously run on this branch,
    the remembered PR numbers will be used.
    --reset (optional): Resets the integration branch and starts from beginning
        of pull request list. Pull requestw already rebased are used without rebase.
        Delete the rebase branch in case you want to trigger a new rebase.
        (use "--reset" or "-r").
    --delete (optional): Deletes all branches created for PR integration
        (use "--delete" or "-d").

Progress Tracking:
    - A branch-specific progress file (e.g., <branch>_progress.json) is used to
      store the current PR number and step in case of failure.
    - A branch-specific remembered PR file (e.g., <branch>_remembered_prs.json)
      stores the list of PR numbers from the first run.
    - On the next run, the script reads from this file and continues with the
      remembered PR numbers.

Files:
    - <branch>_progress.json: Stores the last failed PR number and step.
    - <branch>_remembered_prs.json: Stores the list of PR numbers from the first run.
    - <branch>_integration.log: Logs the integration process and any errors
      encountered for a specific branch.

Exit Codes:
    - 0: Success.
    - 1: Failure during any step (fetch, rebase, or merge).
    - 2: Failure during branch deletion.
"""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

python_cmd = f"{sys.executable}"
python_bin_directory = Path(sys.executable).parent
pre_commit_cmd = f"{python_bin_directory / "pre-commit"}"
pytest_cmd = f"{python_bin_directory / "pytest"}"
pip_cmd = f"{python_bin_directory / "pip"}"

EOS_GITHUB_URL = "https://github.com/Akkudoktor-EOS/EOS"


# Progress and log files are now prefixed by the integration branch name
def get_progress_file(integration_branch):
    return f"{integration_branch}_progress.json"


def get_remembered_prs_file(integration_branch):
    return f"{integration_branch}_remembered_prs.json"


def get_log_file(integration_branch):
    return f"{integration_branch}_integration.log"


def setup_logging(integration_branch):
    """Sets up logging to a branch-specific file and stdout."""
    # Create a logger
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Create file handler
    file_handler = logging.FileHandler(get_log_file(integration_branch))
    file_handler.setLevel(logging.INFO)

    # Create console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    # Create formatter
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    # Add handlers to logger
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)


def run_command(command, cwd=None):
    """Run a shell command and log the output."""
    logging.info(f"Running command: {' '.join(command)}")
    try:
        result = subprocess.run(
            command, cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        logging.info(f"Command output: {result.stdout.decode()}")
    except subprocess.CalledProcessError as e:
        logging.error(f"Command failed with error: {e.stderr.decode()}")
        return False
    return True


def branch_exists(branch_name):
    """Check if a branch exists locally."""
    result = subprocess.run(["git", "branch", "--list", branch_name], stdout=subprocess.PIPE)
    return branch_name in result.stdout.decode()


def create_integration_branch_if_needed(integration_branch):
    """Create the integration branch if it does not exist."""
    if not branch_exists(integration_branch):
        logging.info(f"Integration branch '{integration_branch}' does not exist. Creating it.")
        # Create the integration branch from 'main' or 'master'
        if branch_exists("main"):
            default_branch = "main"
        elif branch_exists("master"):
            default_branch = "master"
        else:
            logging.error("Neither 'main' nor 'master' branch found.")
            sys.exit(1)

        if not run_command(["git", "switch", default_branch]):
            logging.error(f"Failed to switch to branch {default_branch}")
            return False

        # Assure local default_branch is on remote default_branch
        if not run_command(["git", "pull", f"{EOS_GITHUB_URL}", default_branch]):
            logging.error(f"Failed to pull main from {EOS_GITHUB_URL}.")
            return False

        if not run_command(["git", "checkout", "-b", integration_branch, default_branch]):
            logging.error(
                f"Failed to create integration branch {integration_branch} from {default_branch}"
            )
            return False
    else:
        logging.info(f"Integration branch '{integration_branch}' exists.")
    return True


def fetch_pr(pr_number):
    """Fetch the pull request into a local branch."""
    pr_branch = f"pr-{pr_number}"
    logging.info(f"Fetching PR #{pr_number} into branch {pr_branch}")

    # Fetch the pull request and create a new branch for it
    if not run_command(["git", "fetch", f"{EOS_GITHUB_URL}", f"pull/{pr_number}/head:{pr_branch}"]):
        logging.error(f"Failed to fetch PR #{pr_number}")
        return None
    return pr_branch


def create_and_rebase_branch(pr_branch, integration_branch):
    """Create a branch from the PR branch and rebase it onto the integration branch."""
    rebase_branch = f"{integration_branch}-rebase-{pr_branch}"
    logging.info(
        f"Creating and rebasing {rebase_branch} from {pr_branch} onto {integration_branch}"
    )

    # Create a new branch from the PR branch
    if branch_exists(rebase_branch):
        if not run_command(["git", "switch", rebase_branch]):
            logging.error(f"Failed to switch to branch {rebase_branch}")
            return False
        logging.info(f"Rebase branch '{rebase_branch}' exists.")
    else:
        if not run_command(["git", "switch", "-c", rebase_branch, pr_branch]):
            logging.error(f"Failed to create branch {rebase_branch}")
            return False

    # Rebase the new branch onto the integration branch
    if not run_command(["git", "rebase", integration_branch]):
        logging.error(f"Failed to rebase {rebase_branch} onto {integration_branch}")
        return False

    return rebase_branch


def test_branch(rebase_branch, integration_branch):
    """Test the rebased branch."""
    logging.info(f"Testing {rebase_branch}")

    # Checkout the rebased branch
    if not run_command(["git", "checkout", rebase_branch]):
        logging.error(f"Failed to checkout {rebase_branch}.")
        return False

    # Test the rebased branch
    if not run_command([pytest_cmd, "-vs", "--cov", "src", "--cov-report", "term-missing"]):
        logging.error(f"Failed to test {rebase_branch}.")
        return False

    return True


def merge_branch(rebase_branch, integration_branch):
    """Merge the rebased branch into the integration branch."""
    logging.info(f"Merging {rebase_branch} into {integration_branch}")

    # Checkout the integration branch
    if not run_command(["git", "checkout", integration_branch]):
        logging.error(f"Failed to checkout {integration_branch}")
        return False

    # Merge the rebased branch into the integration branch
    if not run_command(["git", "merge", "--no-ff", rebase_branch]):
        logging.error(f"Failed to merge {rebase_branch} into {integration_branch}")
        return False

    return True


def reset_integration_branch(integration_branch):
    """Reset the integration branch to the main branch."""
    logging.info(f"Resetting {integration_branch} to main branch.")

    # Checkout the main branch
    if not run_command(["git", "checkout", "main"]):
        logging.error("Failed to checkout main branch.")
        return False

    # Assure local main is on remote main
    if not run_command(["git", "pull", f"{EOS_GITHUB_URL}", "main"]):
        logging.error(f"Failed to pull main from {EOS_GITHUB_URL}.")
        return False

    # Reset the integration branch to main
    if not run_command(["git", "branch", "-D", integration_branch]):
        logging.error(f"Failed to delete the integration branch {integration_branch}.")
        return False

    # Create a new integration branch from main
    if not run_command(["git", "checkout", "-b", integration_branch]):
        logging.error(f"Failed to create a new integration branch {integration_branch} from main.")
        return False

    return True


def save_progress(step, pr_number, integration_branch):
    """Save the current step and PR number to the branch-specific progress file."""
    progress_file = get_progress_file(integration_branch)
    with open(progress_file, "w") as f:
        json.dump({"step": step, "pr_number": pr_number}, f)


def load_progress(integration_branch):
    """Load the last saved step and PR number from the branch-specific progress file."""
    progress_file = get_progress_file(integration_branch)
    if os.path.exists(progress_file):
        with open(progress_file, "r") as f:
            return json.load(f)
    return None


def save_remembered_prs(pr_numbers, integration_branch):
    """Save the list of PR numbers to the branch-specific remembered PR file."""
    remembered_prs_file = get_remembered_prs_file(integration_branch)
    with open(remembered_prs_file, "w") as f:
        json.dump(pr_numbers, f)


def load_remembered_prs(integration_branch):
    """Load the list of PR numbers from the branch-specific remembered PR file."""
    remembered_prs_file = get_remembered_prs_file(integration_branch)
    if os.path.exists(remembered_prs_file):
        with open(remembered_prs_file, "r") as f:
            return json.load(f)
    return None


def delete_created_branches(pr_numbers, integration_branch):
    """Delete all branches created during the integration process."""
    logging.info(f"Deleting branches created for integration of PRs: {pr_numbers}")

    for pr_number in pr_numbers:
        pr_branch = f"pr-{pr_number}"
        rebase_branch = f"{integration_branch}-rebase-pr-{pr_number}"

        # Delete the PR branch
        if branch_exists(pr_branch):
            if not run_command(["git", "branch", "-D", pr_branch]):
                logging.error(f"Failed to delete branch {pr_branch}")
                return False

        # Delete the rebase branch
        if branch_exists(rebase_branch):
            if not run_command(["git", "branch", "-D", rebase_branch]):
                logging.error(f"Failed to delete branch {rebase_branch}")
                return False

    logging.info("All created branches have been deleted.")
    return True


def delete_log_files(integration_branch):
    """Delete all log files related to the integration branch."""
    logging.info(f"Deleting log files for integration branch '{integration_branch}'.")
    files_to_delete = [
        get_progress_file(integration_branch),
        get_remembered_prs_file(integration_branch),
        get_log_file(integration_branch),
    ]

    for file in files_to_delete:
        try:
            if os.path.exists(file):
                os.remove(file)
                logging.info(f"Deleted file: {file}")
            else:
                logging.warning(f"File not found: {file}")
        except Exception as e:
            logging.error(f"Error deleting file {file}: {str(e)}")


def install_pre_commit_hook():
    """Installs the 'pre-commit' hook in the current Git repository.

    If the hook is missing, installs 'pre-commit' and sets up the hook.
    """
    try:
        # Check if the repository has a .pre-commit-config.yaml file
        if not os.path.isfile(".pre-commit-config.yaml"):
            logging.error(
                "No .pre-commit-config.yaml found in the repository. Please create the configuration file."
            )
            return False

        # Check if pre-commit is installed
        try:
            pre_commit_installed = run_command([pre_commit_cmd, "--version"])
            if pre_commit_installed:
                logging.info("pre-commit is installed.")
            else:
                raise Exception
        except Exception:
            logging.info("pre-commit is not installed, installing now...")
            run_command([pip_cmd, "install", "pre-commit"])
            logging.info("pre-commit installed.")

        # Check if the pre-commit hook is set in the Git hooks directory
        git_hooks_path = os.path.join(".git", "hooks", "pre-commit")
        if os.path.isfile(git_hooks_path):
            logging.info("pre-commit hook is set.")
        else:
            logging.info("pre-commit hook is not set, setting it up now...")
            run_command([pre_commit_cmd, "install"])
            logging.info("pre-commit hook has been installed.")

    except subprocess.CalledProcessError:
        logging.error("An error occurred while checking or installing pre-commit or the hook.")
        return False

    return True


def integrate_prs(pr_numbers, integration_branch):
    """Main function to integrate pull requests into an integration branch."""
    logging.info(f"Starting integration process for PRs: {pr_numbers} into {integration_branch}")
    logging.info(f"Using python from `{python_cmd}`.")
    logging.info(f"Using pytest from `{pytest_cmd}`.")
    logging.info(f"Using pre-commit from `{pre_commit_cmd}`.")
    logging.info(f"Using pip from `{pip_cmd}`.")

    # Load and or save remembered PRs
    remembered_prs = load_remembered_prs(integration_branch)
    if not pr_numbers:
        pr_numbers = remembered_prs
        if not pr_numbers:
            logging.error(
                f"No PR numbers provided and no remembered PRs found for branch {integration_branch}."
            )
            sys.exit(1)

    if remembered_prs and remembered_prs != pr_numbers:
        logging.warning(
            f"Other PR numbers provided than remembered PRs {remembered_prs} found for branch {integration_branch}."
        )
        logging.warning(
            f"Restarting integration process for PRs: {pr_numbers} into {integration_branch}."
        )
        delete_log_files(integration_branch)
        save_remembered_prs(pr_numbers, integration_branch)
        save_progress("reset-branch", None, integration_branch)
    elif not remembered_prs:
        # Remember PR numbers on the first run
        save_remembered_prs(pr_numbers, integration_branch)

    # Load progress from the last run
    progress = load_progress(integration_branch)
    last_step = progress["step"] if progress else None
    last_pr = progress["pr_number"] if progress else None

    if last_step == "integration-finalyzed":
        logging.info(f"PRs {pr_numbers} already successfully integrated into {integration_branch}.")
        logging.info("To re-run the integration:")
        logging.info(f">>> python integrate_prs.py {integration_branch} --reset")
        exit(0)

    if last_step:
        last_pr_str = f" for PR #{last_pr}" if last_pr else ""
        logging.info(f"Resuming from step {last_step}{last_pr_str}.")

    # Reset branch due to changes
    if last_step == "reset-branch":
        reset = reset_integration_branch(integration_branch)
        if not reset:
            sys.exit(1)
        last_step = None

    # Assure pre-commit hook is set.
    if not last_step or last_step == "install-pre-commit":
        installed = install_pre_commit_hook()
        if not installed:
            save_progress("install-pre-commit", None, integration_branch)
            sys.exit(1)
        last_step = None

    # Create or checkout the integration branch
    if not last_step or last_step == "create-branch":
        available = create_integration_branch_if_needed(integration_branch)
        if not available:
            save_progress("create-branch", None, integration_branch)
            sys.exit(1)
        last_step = None

    for pr_number in pr_numbers:
        if last_pr and pr_number < last_pr:
            # Skip PRs processed before the last failure
            logging.info(f"Skipping PR #{pr_number}. Already processed.")
            continue

        logging.info(f"Processing PR #{pr_number}")

        # Step 1: Fetch PR
        if not last_step or last_step == "fetch":
            pr_branch = fetch_pr(pr_number)
            if pr_branch is None:
                save_progress("fetch", pr_number, integration_branch)
                sys.exit(1)
        else:
            pr_branch = f"pr-{pr_number}"

        # Step 2: Rebase PR onto integration branch
        if not last_step or last_step == "rebase":
            rebase_branch = create_and_rebase_branch(pr_branch, integration_branch)
            if not rebase_branch:
                save_progress("rebase", pr_number, integration_branch)
                sys.exit(1)
        else:
            rebase_branch = f"{integration_branch}-rebase-{pr_branch}"

        # Step 3: Test rebased branch
        if not last_step or last_step == "test":
            if not test_branch(rebase_branch, integration_branch):
                save_progress("test", pr_number, integration_branch)
                sys.exit(1)

        # Step 4: Merge rebased branch into integration branch
        if not last_step or last_step == "merge":
            if not merge_branch(rebase_branch, integration_branch):
                save_progress("merge", pr_number, integration_branch)
                sys.exit(1)

        last_step = None

    logging.info(f"All PRs {pr_numbers} successfully integrated into {integration_branch}.")
    save_progress("integration-finalyzed", None, integration_branch)

    # Finally switch back to integration path
    if not run_command(["git", "checkout", integration_branch]):
        logging.error(f"Failed to switch to integration branch {integration_branch}.")
        save_progress(None, None, integration_branch)
        sys.exit(1)

    logging.info("All done.")


def main():
    if len(sys.argv) < 2:
        print("Usage: python integrate_prs.py <integration-branch> [<pr-number> ...]")
        sys.exit(1)

    integration_branch = sys.argv[1]
    pr_numbers = [int(pr) for pr in sys.argv[2:] if pr.isdigit()]
    delete_flag = (
        sys.argv[2].lower()
        if len(sys.argv) > 2 and sys.argv[2].lower() in ["-d", "--delete"]
        else None
    )
    reset_flag = (
        sys.argv[2].lower()
        if len(sys.argv) > 2 and sys.argv[2].lower() in ["-r", "--reset"]
        else None
    )

    # Setup logging
    setup_logging(integration_branch)

    if delete_flag:
        # If delete option is passed, delete the created branches
        pr_numbers = load_remembered_prs(integration_branch)
        if pr_numbers:
            if delete_created_branches(pr_numbers, integration_branch):
                logging.info(
                    f"All branches related to PRs {pr_numbers} for {integration_branch} have been deleted."
                )
                sys.exit(0)
            else:
                logging.error("Failed to delete some branches.")
                sys.exit(2)
        else:
            logging.error(f"No remembered PRs found for branch {integration_branch}.")
            sys.exit(1)
    elif reset_flag:
        save_progress("reset-branch", None, integration_branch)

    # Perform the PR integration process
    integrate_prs(pr_numbers, integration_branch)


if __name__ == "__main__":
    main()
