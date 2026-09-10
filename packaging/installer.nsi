Unicode true
!include "MUI2.nsh"
!define MUI_ICON "${__FILEDIR__}\..\desktop_app\assets\app.ico"
!define MUI_UNICON "${__FILEDIR__}\..\desktop_app\assets\app.ico"
!ifndef BUILD_DIR
!error "BUILD_DIR must point to the PyInstaller onedir output"
!endif
!ifndef OUTPUT_FILE
!error "OUTPUT_FILE must name the installer artifact"
!endif
!ifndef UNINSTALL_LIST
!error "UNINSTALL_LIST must list only files owned by this installer"
!endif
Name "CosyVoice 配音工作台"
OutFile "${OUTPUT_FILE}"
InstallDir "$LOCALAPPDATA\Programs\CosyVoice-Desktop"
RequestExecutionLevel user
SetCompressor /SOLID lzma
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"
Section "Application"
  SetOutPath "$INSTDIR"
  File /r "${BUILD_DIR}\*.*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\CosyVoice 配音工作台.lnk" "$INSTDIR\CosyVoice-Desktop.exe"
  CreateDirectory "$SMPROGRAMS\CosyVoice 配音工作台"
  CreateShortcut "$SMPROGRAMS\CosyVoice 配音工作台\CosyVoice 配音工作台.lnk" "$INSTDIR\CosyVoice-Desktop.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CosyVoice-Desktop" "DisplayName" "CosyVoice 配音工作台"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CosyVoice-Desktop" "UninstallString" '"$INSTDIR\Uninstall.exe"'
SectionEnd
Section "Uninstall"
  Delete "$DESKTOP\CosyVoice 配音工作台.lnk"
  Delete "$SMPROGRAMS\CosyVoice 配音工作台\CosyVoice 配音工作台.lnk"
  RMDir "$SMPROGRAMS\CosyVoice 配音工作台"
  !include "${UNINSTALL_LIST}"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CosyVoice-Desktop"
SectionEnd
