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
!ifndef APP_VERSION
!error "APP_VERSION must match the desktop application version"
!endif
!ifndef APP_FILE_VERSION
!error "APP_FILE_VERSION must contain the numeric Windows version"
!endif
Name "CosyVoice 配音工作台 ${APP_VERSION}"
VIProductVersion "${APP_FILE_VERSION}"
VIAddVersionKey /LANG=1033 "ProductName" "CosyVoice 配音工作台"
VIAddVersionKey /LANG=1033 "ProductVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=1033 "FileDescription" "CosyVoice 配音工作台安装程序"
VIAddVersionKey /LANG=1033 "FileVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=1033 "LegalCopyright" "See LICENSE and NOTICE"
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
  ; v0.1.2 no longer ships the six legacy FLEURS/AISHELL samples. Remove
  ; their package-owned directories when upgrading an older installation;
  ; user voices live in the separate user-data directory and are untouched.
  RMDir /r "$INSTDIR\_internal\voice_library\aishell3_sample_01"
  RMDir /r "$INSTDIR\_internal\voice_library\aishell3_sample_02"
  RMDir /r "$INSTDIR\_internal\voice_library\aishell3_sample_03"
  RMDir /r "$INSTDIR\_internal\voice_library\aishell3_sample_04"
  RMDir /r "$INSTDIR\_internal\voice_library\fleurs_female_01"
  RMDir /r "$INSTDIR\_internal\voice_library\fleurs_male_01"
  ; Older development packages could have placed data at the onedir root.
  ; Clean those exact legacy directories as well, without touching the
  ; remaining manifest or user data.
  RMDir /r "$INSTDIR\voice_library\aishell3_sample_01"
  RMDir /r "$INSTDIR\voice_library\aishell3_sample_02"
  RMDir /r "$INSTDIR\voice_library\aishell3_sample_03"
  RMDir /r "$INSTDIR\voice_library\aishell3_sample_04"
  RMDir /r "$INSTDIR\voice_library\fleurs_female_01"
  RMDir /r "$INSTDIR\voice_library\fleurs_male_01"
  SetOutPath "$INSTDIR"
  File /r "${BUILD_DIR}\*.*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\CosyVoice 配音工作台.lnk" "$INSTDIR\CosyVoice-Desktop.exe"
  CreateDirectory "$SMPROGRAMS\CosyVoice 配音工作台"
  CreateShortcut "$SMPROGRAMS\CosyVoice 配音工作台\CosyVoice 配音工作台.lnk" "$INSTDIR\CosyVoice-Desktop.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CosyVoice-Desktop" "DisplayName" "CosyVoice 配音工作台"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CosyVoice-Desktop" "DisplayVersion" "${APP_VERSION}"
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
