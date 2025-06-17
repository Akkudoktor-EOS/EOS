#!/usr/bin/env python3

import subprocess
import sys

MESSAGE_PREFIX = "Converted to annotated tag:"

def run(cmd, capture_output=False):
    """Run a shell command and return output if needed."""
    result = subprocess.run(cmd, shell=True, check=True, text=True, capture_output=capture_output)
    return result.stdout.strip() if capture_output else None

def get_all_tags():
    """Return a list of all tags."""
    return run("git tag", capture_output=True).splitlines()

def is_lightweight(tag):
    """Return True if a tag is lightweight (points to commit, not tag object)."""
    return run(f"git cat-file -t {tag}", capture_output=True) == "commit"

def get_commit_of_tag(tag):
    """Return the commit SHA a tag points to."""
    return run(f"git rev-list -n 1 {tag}", capture_output=True)

def convert_tag(tag):
    """Delete and recreate a tag as annotated."""
    commit = get_commit_of_tag(tag)
    print(f"Converting {tag} -> annotated ({commit})")
    run(f"git tag -d {tag}")
    run(f'git tag -a {tag} -m "{MESSAGE_PREFIX} {tag}" {commit}')

def main():
    dry_run = "--dry-run" in sys.argv
    push = "--push" in sys.argv

    tags = get_all_tags()
    lightweight_tags = [t for t in tags if is_lightweight(t)]

    if not lightweight_tags:
        print("✅ No lightweight tags found.")
        return

    print("🔍 Lightweight tags found:\n  " + "\n  ".join(lightweight_tags))

    if dry_run:
        print("\n📝 Dry run: No changes will be made.")
        return

    confirm = input("\n⚠️ Convert ALL of these tags to annotated? (y/N): ").lower()
    if confirm != "y":
        print("❌ Aborted.")
        return

    for tag in lightweight_tags:
        convert_tag(tag)

    print("\n✅ Conversion complete.")

    if push:
        print("📤 Pushing updated tags to origin (force)...")
        run("git push origin --tags --force")
        print("✅ Tags pushed.")
    else:
        print("\n🚀 To push changes, run:\n  git push origin --tags --force")

if __name__ == "__main__":
    print("=== Lightweight Tag Converter ===")
    print("Usage: python convert_lightweight_tags.py [--dry-run] [--push]\n")
    main()
