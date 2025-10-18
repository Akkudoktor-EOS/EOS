% SPDX-License-Identifier: Apache-2.0
(release-page)=

# Release Process

This document describes how to prepare and publish a new release **via a Pull Request from a fork**,
using **Commitizen** to manage versioning and changelogs — and how to set a **development version** after the release.

## ✅ Overview of the Process

| Step | Actor       | Action |
|------|-------------|--------|
| 1    | Contributor | Prepare a release branch **in your fork** using Commitizen |
| 2    | Contributor | Open a **Pull Request to upstream** (`akkudoktor/akkudoktoreos`) |
| 3    | Maintainer  | Review and **merge the release PR** |
| 4    | Maintainer  | Create the **GitHub Release and tag** |
| 5    | Maintainer  | Set the **development version marker** via a follow-up PR |

## 🔄 Detailed Workflow

### 1️⃣ Contributor: Prepare the Release in Your Fork

**Clone and sync your fork:**

```bash
git clone git@github.com:<your-username>/akkudoktoreos.git
cd akkudoktoreos
git remote add upstream git@github.com:akkudoktor/akkudoktoreos.git

git fetch upstream
git checkout main
git pull upstream main
````

**Create the release branch:**

```bash
git checkout -b release/vX.Y.Z
```

**Run Commitizen to bump version and update changelog:**

```bash
make bump
```

> ✅ This updates version files and `CHANGELOG.md` in a single commit.
> 🚫 **Do not push tags** — tags are created by the maintainer via GitHub Releases.

**Push the branch to your fork:**

```bash
git push origin release/vX.Y.Z
```

### 2️⃣ Contributor: Open the Release Pull Request

| From                           | To                              |
| ------------------------------ | ------------------------------- |
| `your-username:release/vX.Y.Z` | `akkudoktor/akkudoktoreos:main` |

**PR Title:**

```text
Release vX.Y.Z
```

**PR Description Template:**

```markdown
## Release vX.Y.Z

This pull request prepares release **vX.Y.Z**.

### Changes
- Version bump via Commitizen
- Changelog update

### Changelog Summary
<!-- Copy key highlights from CHANGELOG.md here -->

See `CHANGELOG.md` for full details.
```

### 3️⃣ Maintainer: Review and Merge the Release PR

**Review Checklist:**

* ✅ Only version files and `CHANGELOG.md` are modified
* ✅ Version numbers are consistent
* ✅ Changelog is complete and properly formatted
* ✅ No unrelated changes are included

**Merge Strategy:**

* Prefer **Merge Commit** (or **Squash Merge**, per project preference)
* Use commit message: `Release vX.Y.Z`

### 4️⃣ Maintainer: Publish the GitHub Release

1. Go to **GitHub → Releases → Draft a new release**
2. **Choose tag** → enter `vX.Y.Z` (GitHub creates the tag on publish)
3. **Release title:** `vX.Y.Z`
4. **Paste changelog entry** from `CHANGELOG.md`
5. Optionally enable **Set as latest release**
6. Click **Publish release** 🎉

### 5️⃣ Maintainer: Prepare the Development Version Marker

**Sync local copy:**

```bash
git fetch upstream
git checkout main
git pull upstream main
```

**Create a development version branch:**

```bash
git checkout -b release/vX.Y.Z_dev
```

**Set development marker manually (example for `pyproject.toml`):**

```bash
sed -i 's/version = "\(.*\)"/version = "\1+dev"/' pyproject.toml
git add pyproject.toml
git commit -m "chore: set development version marker"
git push origin release/vX.Y.Z_dev
```

### 6️⃣ Maintainer (or Contributor): Open the Development Version PR

| From                               | To                              |
| ---------------------------------- | ------------------------------- |
| `your-username:release/vX.Y.Z_dev` | `akkudoktor/akkudoktoreos:main` |

**PR Title:**

```text
Release vX.Y.Z+dev
```

**PR Description Template:**

```markdown
## Release vX.Y.Z_dev

This pull request marks the repository as back in active development.

### Changes
- Set version to `vX.Y.Z+dev`

No changelog entry is needed.
```

### 7️⃣ Maintainer: Review and Merge the Development Version PR

**Checklist:**

* ✅ Only version files updated to `+dev`
* ✅ No unintended changes

**Merge Strategy:**

* Merge with commit message: `Release vX.Y.Z_dev`

## ✅ Quick Reference

| Step | Actor | Action |
| ---- | ----- | ------ |
| **1. Prepare release branch** | Contributor | Bump version & changelog via Commitizen |
| **2. Open release PR** | Contributor | Submit release for review |
| **3. Review & merge release PR** | Maintainer | Finalize changes into `main` |
| **4. Publish GitHub Release** | Maintainer | Create tag & notify users |
| **5. Prepare development version branch** | Maintainer | Set development marker |
| **6. Open development PR** | Maintainer (or Contributor) | Propose returning to development state |
| **7. Review & merge development PR** | Maintainer | Mark repository as back in development |
