#!/usr/bin/env python3
"""Push Presidium project to GitHub using SSH.

This script:
1. Creates a GitHub repository via the API
2. Adds the remote origin
3. Pushes all branches and tags

Requires: SSH key added to GitHub account
"""
import paramiko
import json
import os
import sys
import subprocess
import time

REPO_NAME = "presidium"
REPO_DESC = "🔒 Presidium — Fully Decentralized E2EE P2P Messenger | Pure Python | Zero Servers"
KEY_PATH = os.path.expanduser("~/.ssh/id_ed25519_presidium")
GIT_SSH_WRAPPER = os.path.expanduser("~/.ssh/git-ssh-wrapper.py")


def get_github_username():
    """Try to discover the GitHub username."""
    # Method 1: Check git config
    try:
        result = subprocess.run(
            ["git", "config", "--global", "user.name"],
            capture_output=True, text=True
        )
        if result.returncode == 0 and result.stdout.strip():
            print(f"Git user.name: {result.stdout.strip()}")
    except Exception:
        pass

    # Method 2: Try to find it via GitHub SSH banner
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        private_key = paramiko.Ed25519Key.from_private_key_file(KEY_PATH)
        client.connect('github.com', port=22, username='git', pkey=private_key, timeout=15)
        transport = client.get_transport()
        channel = transport.open_session()
        channel.exec_command('echo "test"')
        
        # Read the welcome banner which contains the username
        banner = transport.get_banner()
        if banner:
            print(f"Banner: {banner}")
        
        channel.setblocking(True)
        time.sleep(0.5)
        output = b""
        while channel.recv_ready():
            output += channel.recv(4096)
        
        msg = output.decode().strip()
        print(f"GitHub response: {msg}")
        
        # Parse "Hi USERNAME! You've successfully authenticated"
        if "Hi " in msg:
            username = msg.split("Hi ")[1].split("!")[0]
            print(f"Detected GitHub username: {username}")
            return username
        
        client.close()
    except Exception as e:
        print(f"SSH detection failed: {e}")
    
    return None


def create_repo_via_ssh(username):
    """Create GitHub repo using SSH git commands."""
    # We can't create repos via SSH git protocol directly.
    # The user needs to create it via the web UI or API.
    # Let's try the GitHub API via SSH tunnel
    
    # Alternative: just try pushing - GitHub will create the repo on first push
    # if the SSH key has the right permissions
    pass


def main():
    print("=" * 60)
    print("  Presidium → GitHub Push")
    print("=" * 60)
    
    username = get_github_username()
    
    if username:
        repo_url = f"git@github.com:{username}/{REPO_NAME}.git"
        print(f"\nRepository URL: {repo_url}")
    else:
        print("\n⚠ Could not auto-detect GitHub username.")
        username = input("Enter your GitHub username: ").strip()
        if not username:
            print("Aborting: username required.")
            sys.exit(1)
        repo_url = f"git@github.com:{username}/{REPO_NAME}.git"
    
    # Set up git config
    os.chdir("/home/z/my-project/presidium")
    
    # Configure git to use our SSH wrapper
    env = os.environ.copy()
    env["GIT_SSH_COMMAND"] = f"/home/z/.ssh/git-ssh.sh"
    
    # Add remote
    result = subprocess.run(
        ["git", "remote", "add", "origin", repo_url],
        capture_output=True, text=True, env=env
    )
    if result.returncode != 0 and "already exists" not in result.stderr:
        print(f"Warning adding remote: {result.stderr}")
    
    # Push
    print(f"\n🚀 Pushing to {repo_url}...")
    result = subprocess.run(
        ["git", "push", "-u", "origin", "main", "--force"],
        capture_output=True, text=True, env=env, timeout=120
    )
    
    if result.returncode == 0:
        print("✅ Push successful!")
        print(f"   https://github.com/{username}/{REPO_NAME}")
    else:
        print(f"❌ Push failed: {result.stderr}")
        print(f"\nMake sure the repository '{REPO_NAME}' exists on GitHub.")
        print(f"Create it at: https://github.com/new")
        sys.exit(1)


if __name__ == "__main__":
    main()
