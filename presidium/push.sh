#!/bin/bash
# Usage: ./push.sh <github_username>
# Example: ./push.sh octocat

USERNAME=${1:-""}
if [ -z "$USERNAME" ]; then
    echo "Usage: ./push.sh <github_username>"
    exit 1
fi

REPO="git@github.com:$USERNAME/presidium.git"
echo "🚀 Pushing to $REPO"

export GIT_SSH_COMMAND="/home/z/.ssh/git-ssh.sh"

cd /home/z/my-project/presidium

# Remove old remote and add new one
git remote remove origin 2>/dev/null
git remote add origin "$REPO"

# Push
git -c core.sshCommand="/home/z/.ssh/git-ssh.sh" push -u origin main --force

echo "✅ Done! https://github.com/$USERNAME/presidium"
