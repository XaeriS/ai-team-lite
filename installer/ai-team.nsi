; AI Team Lite installer (NSIS). Built by installer/build.sh.
Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "nsDialogs.nsh"
!include "FileFunc.nsh"

!ifndef VERSION
  !define VERSION "0.1.0"
!endif
!define APPNAME "AI Team Lite"
!define UNINSTKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\AITeamLite"
!define WEBVIEW2 "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"

Name "${APPNAME}"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\Programs\${APPNAME}"
InstallDirRegKey HKCU "Software\AITeamLite" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 64
BrandingText "${APPNAME} ${VERSION}"
VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "${APPNAME}"
VIAddVersionKey "FileDescription" "${APPNAME} installer"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "Personal build"

!define MUI_ICON "${STAGE}\app\ui\icon.ico"
!define MUI_UNICON "${STAGE}\app\ui\icon.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Welcome to ${APPNAME}"
!define MUI_WELCOMEPAGE_TEXT "AI Team Lite by Fred turns your ideas into working prototypes. You talk (or type) with Fred, your AI team lead: he makes sure the problem is worth solving, then his AI team plans, builds and tests it with you.$\r$\n$\r$\nThis is a prototype: try it on a test machine or in a separate folder, and keep real business data away from it.$\r$\n$\r$\nEverything needed is included. Click Next to choose where to install it."
!define MUI_DIRECTORYPAGE_TEXT_TOP "Choose the folder where AI Team Lite will be installed. No administrator rights are needed. Your projects are stored separately (you choose that folder with Fred later)."
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION LaunchApp
!define MUI_FINISHPAGE_RUN_TEXT "Start AI Team Lite now"
!define MUI_FINISHPAGE_TEXT "A shortcut named AI Team Lite is on your desktop."

Var ResetFlag
Var ResetBox
Var HasPrevious
; optional extras (item 70): 1 = download, 0 = skip; *Have = already on this PC
Var WantVoice
Var WantOllama
Var WantModel
Var HaveVoice
Var HaveOllama
Var BoxVoice
Var BoxOllama
Var BoxModel

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
Page custom FreshPageShow FreshPageLeave
Page custom ExtrasPageShow ExtrasPageLeave
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

!macro CloseApp
  ; stop AI Team Lite and its Claude engine if they run from this folder (otherwise its files stay locked)
  nsExec::Exec `taskkill /F /T /IM "${APPNAME}.exe"`
  Pop $0
  nsExec::Exec `powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Process python,pythonw,claude,msedgewebview2,node -ErrorAction SilentlyContinue | Where-Object { $$_.Path -like '$INSTDIR\*' } | Stop-Process -Force"`
  Pop $0
  Sleep 2500
!macroend

; Remove the previous program files, making sure nothing is still holding them. Your settings and projects
; live elsewhere and are not touched here.
!macro RemovePrevious
  retry_remove:
  !insertmacro CloseApp
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\python"
  RMDir /r "$INSTDIR\git"
  RMDir /r "$INSTDIR\node"
  RMDir /r "$INSTDIR\archify"
  IfFileExists "$INSTDIR\python\python3.dll" 0 done_remove
    MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "AI Team Lite is still open, so its files cannot be replaced.$\r$\n$\r$\nPlease close AI Team Lite completely - check the system tray (next to the clock) and, if needed, end any $\"AI Team Lite$\", pythonw.exe or python.exe task running from this folder - then click Retry." IDRETRY retry_remove
    Abort
  done_remove:
!macroend

Function .onInit
  StrCpy $ResetFlag "0"
  StrCpy $HasPrevious "0"
  StrCpy $WantVoice "1"
  StrCpy $WantOllama "1"
  StrCpy $WantModel "0"
  ; already on this PC? then there is nothing to download
  StrCpy $HaveVoice "0"
  IfFileExists "$APPDATA\${APPNAME}\voice-models\base\model.bin" 0 +2
    StrCpy $HaveVoice "1"
  StrCpy $HaveOllama "0"
  IfFileExists "$LOCALAPPDATA\Programs\Ollama\ollama.exe" 0 +2
    StrCpy $HaveOllama "1"
  ${If} $HaveOllama == "0"
    nsExec::Exec 'cmd /c where ollama'
    Pop $0
    ${If} $0 == "0"
      StrCpy $HaveOllama "1"
    ${EndIf}
  ${EndIf}
  ${If} ${FileExists} "$APPDATA\${APPNAME}\*.*"
    StrCpy $HasPrevious "1"
  ${EndIf}
  ReadRegStr $0 HKCU "Software\AITeamLite" "InstallDir"
  ${If} $0 != ""
    StrCpy $HasPrevious "1"
  ${EndIf}
  ; silent / command line:  "AI Team Lite Setup.exe" /S /RESET  -> also start fresh
  ${GetParameters} $1
  ClearErrors
  ${GetOptions} $1 "/RESET" $2
  ${IfNot} ${Errors}
    StrCpy $ResetFlag "1"
  ${EndIf}
FunctionEnd

Function FreshPageShow
  ${If} $HasPrevious != "1"
    Abort  ; first install: nothing to reset, skip this page
  ${EndIf}
  !insertmacro MUI_HEADER_TEXT "AI Team Lite was installed before" "Keep your setup, or start completely fresh."
  nsDialogs::Create 1018
  Pop $0
  ${NSD_CreateLabel} 0 0 100% 84u "The program files are always replaced with this new version.$\r$\n$\r$\nYou can also erase everything AI Team Lite remembers on this PC: its settings, your saved Anthropic key and GitHub token, the Claude subscription sign-in, Fred's setup chat and the logs. AI Team Lite will then start like the very first time.$\r$\n$\r$\nYour project folders are never deleted."
  Pop $0
  ${NSD_CreateCheckbox} 0 94u 100% 14u "Start completely fresh (erase settings, saved keys, sign-in and setup chat)"
  Pop $ResetBox
  ${If} $ResetFlag == "1"
    ${NSD_Check} $ResetBox
  ${EndIf}
  nsDialogs::Show
FunctionEnd

Function FreshPageLeave
  ${NSD_GetState} $ResetBox $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $ResetFlag "1"
  ${Else}
    StrCpy $ResetFlag "0"
  ${EndIf}
FunctionEnd

Function ExtrasPageShow
  !insertmacro MUI_HEADER_TEXT "Extras" "Download these now so everything works right away (you can also get them later)."
  nsDialogs::Create 1018
  Pop $0
  ${NSD_CreateLabel} 0 0 100% 24u "AI Team Lite checks your PC first: anything already installed is not downloaded again. If a download does not work now, AI Team Lite offers it again when you first need it."
  Pop $0
  ${If} $HaveVoice == "1"
    ${NSD_CreateCheckbox} 0 30u 100% 14u "Voice - talk to Fred: speech model already on this PC"
    Pop $BoxVoice
    EnableWindow $BoxVoice 0
  ${Else}
    ${NSD_CreateCheckbox} 0 30u 100% 14u "Voice - talk to Fred instead of typing (speech model, about 145 MB; runs on your PC)"
    Pop $BoxVoice
    ${NSD_Check} $BoxVoice
  ${EndIf}
  ${If} $HaveOllama == "1"
    ${NSD_CreateCheckbox} 0 50u 100% 14u "Ollama - free local brains: already installed on this PC"
    Pop $BoxOllama
    EnableWindow $BoxOllama 0
  ${Else}
    ${NSD_CreateCheckbox} 0 50u 100% 14u "Ollama - free brains that run on your PC and save Claude usage (about 1 GB)"
    Pop $BoxOllama
    ${NSD_Check} $BoxOllama
  ${EndIf}
  ${NSD_CreateCheckbox} 0 70u 100% 14u "Also download a first local model sized to this PC (several GB - optional)"
  Pop $BoxModel
  nsDialogs::Show
FunctionEnd

Function ExtrasPageLeave
  StrCpy $WantVoice "0"
  StrCpy $WantOllama "0"
  StrCpy $WantModel "0"
  ${If} $HaveVoice == "0"
    ${NSD_GetState} $BoxVoice $0
    ${If} $0 == ${BST_CHECKED}
      StrCpy $WantVoice "1"
    ${EndIf}
  ${EndIf}
  ${If} $HaveOllama == "0"
    ${NSD_GetState} $BoxOllama $0
    ${If} $0 == ${BST_CHECKED}
      StrCpy $WantOllama "1"
    ${EndIf}
  ${EndIf}
  ${NSD_GetState} $BoxModel $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $WantModel "1"
  ${EndIf}
FunctionEnd

Function LaunchApp
  Exec '"$INSTDIR\python\${APPNAME}.exe" "$INSTDIR\app\main.py"'
FunctionEnd

Section "Install"
  SetOutPath "$INSTDIR"
  !insertmacro RemovePrevious
  File /r "${STAGE}\*.*"

  CreateShortCut "$DESKTOP\${APPNAME}.lnk" "$INSTDIR\python\${APPNAME}.exe" '"$INSTDIR\app\main.py"' "$INSTDIR\app\ui\icon.ico" 0 SW_SHOWNORMAL "" "Talk to Fred, your AI team lead"
  CreateDirectory "$SMPROGRAMS\${APPNAME}"
  CreateShortCut "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk" "$INSTDIR\python\${APPNAME}.exe" '"$INSTDIR\app\main.py"' "$INSTDIR\app\ui\icon.ico" 0
  CreateShortCut "$SMPROGRAMS\${APPNAME}\Uninstall ${APPNAME}.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortCut "$SMPROGRAMS\${APPNAME}\Reset ${APPNAME} (start fresh).lnk" "$INSTDIR\python\pythonw.exe" '"$INSTDIR\app\reset.py"' "$INSTDIR\app\ui\icon.ico" 0

  ${If} $ResetFlag == "1"
    DetailPrint "Starting fresh: erasing previous settings, keys and sign-in..."
    nsExec::ExecToLog '"$INSTDIR\python\python.exe" "$INSTDIR\app\reset.py" --yes'
    Pop $0
  ${EndIf}

  ; optional extras (item 70): the script checks again and skips anything already on this PC
  StrCpy $1 ""
  ${If} $WantVoice == "1"
    StrCpy $1 "$1 --voice"
  ${EndIf}
  ${If} $WantOllama == "1"
    StrCpy $1 "$1 --ollama"
  ${EndIf}
  ${If} $WantModel == "1"
    StrCpy $1 "$1 --model"
  ${EndIf}
  ${If} $1 != ""
    DetailPrint "Setting up the extras you chose (this can take a few minutes)..."
    nsExec::ExecToLog '"$INSTDIR\python\python.exe" "$INSTDIR\app\setup_extras.py"$1'
    Pop $0
  ${EndIf}

  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\AITeamLite" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayName" "${APPNAME}"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTKEY}" "Publisher" "AI Team Lite"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayIcon" "$INSTDIR\app\ui\icon.ico"
  WriteRegStr HKCU "${UNINSTKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTKEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "${UNINSTKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTKEY}" "NoRepair" 1

  ; the app window uses Microsoft Edge WebView2 (built into Windows 11 and most Windows 10 PCs)
  ReadRegStr $0 HKLM "SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\${WEBVIEW2}" "pv"
  ReadRegStr $1 HKCU "Software\Microsoft\EdgeUpdate\Clients\${WEBVIEW2}" "pv"
  ${If} $0 == ""
  ${OrIf} $0 == "0.0.0.0"
    ${If} $1 == ""
    ${OrIf} $1 == "0.0.0.0"
      MessageBox MB_YESNO|MB_ICONINFORMATION "AI Team Lite's window needs Microsoft Edge WebView2, which seems to be missing on this PC. Without it AI Team Lite opens in your web browser instead.$\r$\n$\r$\nOpen Microsoft's download page now?" /SD IDNO IDNO +2
        ExecShell "open" "https://developer.microsoft.com/microsoft-edge/webview2/#download"
    ${EndIf}
  ${EndIf}
SectionEnd

Function un.onInit
  StrCpy $ResetFlag "0"
  ${GetParameters} $1
  ClearErrors
  ${GetOptions} $1 "/RESET" $2
  ${IfNot} ${Errors}
    StrCpy $ResetFlag "1"
  ${EndIf}
FunctionEnd

Section "Uninstall"
  !insertmacro CloseApp
  ${If} $ResetFlag != "1"
    MessageBox MB_YESNO|MB_ICONQUESTION "Also erase everything AI Team Lite remembers on this PC (settings, saved Anthropic key and GitHub token, Claude sign-in, setup chat, logs)?$\r$\n$\r$\nYour project folders are kept either way." /SD IDNO IDNO +2
      StrCpy $ResetFlag "1"
  ${EndIf}
  ${If} $ResetFlag == "1"
    nsExec::ExecToLog '"$INSTDIR\python\python.exe" "$INSTDIR\app\reset.py" --yes'
    Pop $0
  ${EndIf}
  Delete "$DESKTOP\${APPNAME}.lnk"
  RMDir /r "$SMPROGRAMS\${APPNAME}"
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\python"
  RMDir /r "$INSTDIR\git"
  RMDir /r "$INSTDIR\node"
  RMDir /r "$INSTDIR\archify"
  Delete "$INSTDIR\README.txt"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "${UNINSTKEY}"
  DeleteRegKey HKCU "Software\AITeamLite"
  ${If} $ResetFlag == "1"
    MessageBox MB_OK "AI Team Lite was removed, along with its settings, saved keys and sign-in. Your project folders were kept." /SD IDOK
  ${Else}
    MessageBox MB_OK "AI Team Lite was removed. Your settings and saved keys were kept, so reinstalling picks up where you left off. Your project folders were kept." /SD IDOK
  ${EndIf}
SectionEnd
