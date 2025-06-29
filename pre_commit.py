#!/usr/bin/env python3
import re
import time
import os

# Paths
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
songui_path = os.path.join(repo_root, 'songui.py')
version_path = os.path.join(repo_root, 'version.txt')

# Update BUILD_TIMESTAMP in songui.py
with open(songui_path, 'r') as f:
    code = f.read()

now = str(int(time.time()))
new_code, n = re.subn(r'(?m)^BUILD_TIMESTAMP\s*=.*$', f'BUILD_TIMESTAMP = "{now}"', code)
if n == 0:
    # Insert after imports if not present
    lines = code.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith('import') and not lines[i+1].strip().startswith('import'):
            lines.insert(i+2, f'BUILD_TIMESTAMP = "{now}"')
            break
    new_code = '\n'.join(lines)

with open(songui_path, 'w') as f:
    f.write(new_code)

# Update version.txt
with open(version_path, 'w') as f:
    f.write(now + '\n')

# Stage the changes
os.system(f'git add "{songui_path}" "{version_path}"')
