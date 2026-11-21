# L47: keeping the team's commands in a box (REQ-22)

Today the team's commands (Python, Git, pip, tests, Claude's engine) run with all of your rights. They can write anywhere you can and read any file you can read. This page compares five ways to limit that.

Your PC runs Windows 11 Home (edition "Core", version 26H2, build 26300). Windows Sandbox is not available on Home.

## What was tested on your PC

On 2026-11-21 a small test program (`tools/sandbox_probe.py`) started each tool inside the box and tried two forbidden things:

- writing a file in a folder next to the test folder
- reading a "private" file placed in another throwaway folder.

It only used throwaway folders in the Windows temp folder. It never touched your Documents, keys or browser data. Afterwards it removed everything it had set up, and a separate check confirmed it.

| Check | B. AppContainer | A. Restricted token + job object |
|---|---|---|
| Python starts | pass | pass |
| Git starts | pass | pass |
| pip starts | pass | pass |
| A small test run (pytest) | fail: pytest looks at the folder one level above the project, and the box refused it ("Access is denied") | pass |
| Same test run, box allowed to see only the attributes of that folder above | pass | pass |
| Claude's engine (claude.exe --version) | pass | pass |
| Writing outside the project is refused | pass: refused | fail: the file was written |
| Reading a private file is refused | pass: refused | fail: the file was read |
| Clean-up afterwards | pass | pass |

The raw results are in `reports/L47-probe-results.json`.

## A. Restricted token + job object

**What it protects:** the commands lose the special Windows privileges, and the job object stops them from using the clipboard, changing system settings or shutting Windows down. It does not protect your files. On your PC the probe wrote outside the project and read the private file without any problem, because the commands still run as you. The network stays open, and your part of the registry stays writable.

**What you must do:** nothing.

**Windows editions:** every edition, Home and Pro.

## B. AppContainer

**What it protects:** the commands can only reach folders the app grants them: the project folder, the bundled Python and Git, and Windows system files. On your PC, writing outside the project and reading the private file were both refused. The network is closed unless the app opens it. Your project work needs the network (pip, GitHub, Claude), so the app would open it, which means network access is not protected. The registry is limited to the container's own area. The probe did not test the registry.

**What you must do:** nothing on Windows. The app creates the container itself and needs no administrator rights.

**Windows editions:** Windows 8 and later, every edition, Home included.

**Still to prove before it is built:** whether Claude's engine can sign in and use its own settings folder from inside the box, whether `git push` can reach your saved GitHub login, whether `pip install` works with the network on, and whether apps with a window (WebView2) can start. The pytest problem above has a working fix: the box gets "read attributes" on the folder that holds your projects, with no right to list or open its other contents.

## C. Windows Sandbox

**What it protects:** everything. It is a complete, throwaway copy of Windows: files, registry, programs. Network can be on or off.

**What you must do:** upgrade to Windows 11 Pro (paid), turn the feature on, and restart. The sandbox starts empty each time, so Python, Git and the project would have to be copied in and set up on every start.

**Windows editions:** Pro, Enterprise and Education only. It does not work on your Windows 11 Home.

## D. WSL (Linux inside Windows)

**What it protects:** commands run in a Linux system with its own files. By default WSL can still read and write your Windows drive (C: appears inside it), so your files are only protected if that is switched off. The network is open.

**What you must do:** install WSL (administrator rights and a restart) and keep a Linux copy of Python and Git. The team builds Windows apps, and those cannot be tested from Linux.

**Windows editions:** Home and Pro.

## E. A separate Windows user

**What it protects:** the commands run as a second local account (for example "AI Team worker"). That account cannot open your personal folders, so your private files are safe, and it can only write where it has been allowed. It has its own registry area. The network is open. The probe did not test this option.

**What you must do:** create the account once (administrator rights), give it a password that the app keeps in Windows Credential Manager, and share the projects folder with it. Files it creates belong to that account, which can make them awkward to edit from your own account.

**Windows editions:** every edition, Home and Pro.

## Recommendation

Go with B, AppContainer. On your Windows 11 Home PC it was the only option that refused both forbidden actions while Python, Git, pip and Claude's engine still worked. The one failure, pytest, has a tested fix. It needs no install, no admin rights and no extra account. Option A adds little protection for files, so it only makes sense as a fallback if B cannot work for a tool. Before switching it on by default, the next step proves the open points listed under B: Claude's sign-in, `git push`, `pip install` with the network on, and apps with a window.

This protects your files outside the project from being changed or read but not your network connection or the project folder itself, and needs a yes from you once. Do you want it?
