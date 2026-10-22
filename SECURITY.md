# Security policy

## Supported versions

Only the latest release gets fixes.

## Reporting a problem

Please don't open a public issue for a security problem. Use GitHub's
[private vulnerability reporting](https://github.com/turtlegameworks/Anim2D/security/advisories/new) instead
(**Security → Report a vulnerability** on the repository).

Include what you found, the steps or a file that reproduces it, and the version. You should get a reply
within a week. Once a fix is released we'll credit you in the advisory, unless you'd rather we didn't.

We treat these as security issues: a crafted project, atlas, PSD, preset or `.spine` file that writes or
deletes files outside the folder the user chose, runs code, or exhausts memory; and an installer or
uninstaller that touches files it didn't create.
