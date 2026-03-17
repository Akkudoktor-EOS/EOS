% SPDX-License-Identifier: Apache-2.0

(release-page)=

# Release Process

This document describes how to prepare and publish a new release **via a Pull Request from a fork**,
and how to set a **development version** after the release.

## ✅ Overview of the Process

| Step | Actor       | Action |
|------|-------------|--------|
| 1    | Contributor | Prepare a release branch **in your fork** |
| 2    | Contributor | Open a **Pull Request to upstream** (`Akkudoktor-EOS/EOS`) |
| 3    | Maintainer  | Review and **merge the release PR** |
| 4    | CI          | Create the **GitHub Release and tag** |
| 5    | CI          | Open a **Pull Request to set the development version marker** |
| 6    | Maintainer  | Review and **merge the development version PR** |

## 🔄 Detailed Workflow

### 1️⃣ Contributor: Prepare the Release in Your Fork

#### Clone and sync your fork

```bash
git clone https://github.com/<your-username>/EOS
cd EOS
git remote add eos https://github.com/Akkudoktor-EOS/EOS
git fetch eos
git checkout main
git pull eos main
```

#### Create the release branch

```bash
git checkout -b release/vX.Y.Z
```

#### Bump the version information

Set `__version__` in src/akkudoktoreos/core/version.py

```python
__version__ = 0.3.0
```

Prepare version by updating versioned files, e.g.:

- config.yaml

and the generated documentation:

```bash
make prepare-version
```

Check the changes by:

```bash
make test-version
```

#### Create a new CHANGELOG.md entry

Edit CHANGELOG.md

#### Create the new release commit

Add all the changed version files and all other changes to the commit.

```bash
git add src/akkudoktoreos/core/version.py CHANGELOG.md ...
git commit -s -m "chore: Prepare Release v0.3.0"
```

#### Push the branch to your fork

```bash
git push --set-upstream origin release/v0.3.0
```

### 2️⃣ Contributor: Open the Release Preparation Pull Request

| From                                 | To                        |
| ------------------------------------ | ------------------------- |
| `<your-username>/EOS:release/vX.Y.Z` | `Akkudoktor-EOS/EOS:main` |

**PR Title:**

```text
chore: prepare release vX.Y.Z
```

**PR Description Template:**

```markdown
## Prepare Release vX.Y.Z

This pull request prepares release **vX.Y.Z**.

### Changes

- Version bump
- Changelog update

### Changelog Summary

<!-- Copy key highlights from CHANGELOG.md here -->

See `CHANGELOG.md` for full details.
```

### 3️⃣ Maintainer: Review and Merge the Release PR

**Review Checklist:**

- ✅ Only version files and `CHANGELOG.md` are modified
- ✅ Version numbers are consistent
- ✅ Changelog is complete and properly formatted
- ✅ No unrelated changes are included

**Merge Strategy:**

- Prefer **Merge Commit** (or **Squash Merge**, per project preference)
- Use commit message: `chore: Prepare Release vX.Y.Z`

### 4️⃣ CI: Publish the GitHub Release

The new release will automatically be published by the GitHub CI action.
See `.github/workflows/bump-version.yml` for details.

### 5️⃣ CI: Open Pull Request for Development Version Marker

After tagging the release, the CI automatically opens a Pull Request to bump
the version to the next development version marker `.dev`.
See `.github/workflows/bump-version.yml` for details.

**PR Title:**

```text
chore: bump dev version to vX.Y+1.0.dev
```

### 6️⃣ Maintainer: Review and Merge the Development Version PR

**Review Checklist:**

- ✅ Only `src/akkudoktoreos/core/version.py` is modified
- ✅ Version has `.dev` suffix
- ✅ No unrelated changes are included

**Merge Strategy:**

- Prefer **Merge Commit** (or **Squash Merge**, per project preference)
- Use commit message: `chore: bump dev version to vX.Y+1.0.dev`

## ✅ Quick Reference

| Step | Actor | Action |
| ---- | ----- | ------ |
| **1. Prepare release branch** | Contributor | Bump version & changelog |
| **2. Open release PR** | Contributor | Submit release for review |
| **3. Review & merge release PR** | Maintainer | Finalize changes into `main` |
| **4. Publish GitHub Release** | CI | Create tag & notify users |
| **5. Open development version PR** | CI | Open PR to set development marker |
| **6. Review & merge development version PR** | Maintainer | Merge `.dev` version into `main` |
