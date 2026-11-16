## Install

On Linux:

```bash
curl -fsSL https://github.com/Eymistaken/Pcbridge/releases/latest/download/install.sh | sh
```
<!-- windows -->

On Windows 11, in PowerShell (no administrator rights needed):

```powershell
irm https://github.com/Eymistaken/Pcbridge/releases/latest/download/install.ps1 | iex
```
<!-- /windows -->

This installs the newest release for your user only, or updates an existing install, and then runs `pcbridge setup`. To install this release rather than the newest one, give its version:

```bash
curl -fsSL https://github.com/Eymistaken/Pcbridge/releases/latest/download/install.sh | sh -s -- --version {{VERSION}}
```
<!-- windows -->

```powershell
$env:PCBRIDGE_VERSION = "{{VERSION}}"; irm https://github.com/Eymistaken/Pcbridge/releases/latest/download/install.ps1 | iex
```
<!-- /windows -->

To read and verify the script before running it, or to install the `.deb` or Arch package from the assets below instead, see the [install guide](https://github.com/Eymistaken/Pcbridge/blob/{{VERSION}}/docs/install.md).
