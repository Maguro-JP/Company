; Company の Setup.exe。build.sh から makensis で組む（SRC、OUT、VERSION を渡す）
;
; 置き場
;   %LOCALAPPDATA%\Programs\Company\   プログラムと記録（data\）。管理者権限なしで入り、アプリ自身が書ける
;   data\models\                        内蔵モデル（右パネルから取る。入れ直しても消さない）
;
; 更新: 同じ場所に入れ直す。プログラム（company、ui、agents、python）は消してから置く。data\ は残す
; 消す: プログラムとショートカットと登録を消す。data\（モデル・記録・設定）は聞いてから消す
Target amd64-unicode
Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"

!define APP "Company"
!define UNINST "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP}"

Name "${APP}"
OutFile "${OUT}"
InstallDir "$LOCALAPPDATA\Programs\${APP}"
InstallDirRegKey HKCU "Software\${APP}" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma

!define MUI_ICON "${ICON}"
!define MUI_UNICON "${ICON}"
!define LNK_ICON "$INSTDIR\ui\icon\app.ico"

!define MUI_FINISHPAGE_RUN "$INSTDIR\python\pythonw.exe"
!define MUI_FINISHPAGE_RUN_PARAMETERS "app.py"
!define MUI_FINISHPAGE_RUN_TEXT "Company を起動する"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Japanese"

Section "Install"
  RMDir /r "$INSTDIR\company"
  RMDir /r "$INSTDIR\ui"
  RMDir /r "$INSTDIR\agents"
  RMDir /r "$INSTDIR\python"
  SetOutPath "$INSTDIR"
  File /r "${SRC}\*.*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"

  ; ショートカット。作業ディレクトリをプログラムの場所にする（app.py は自分の場所を基準に data\ を書く）
  SetOutPath "$INSTDIR"
  CreateShortcut "$DESKTOP\${APP}.lnk" "$INSTDIR\python\pythonw.exe" "app.py" "${LNK_ICON}" 0
  CreateDirectory "$SMPROGRAMS\${APP}"
  CreateShortcut "$SMPROGRAMS\${APP}\${APP}.lnk" "$INSTDIR\python\pythonw.exe" "app.py" "${LNK_ICON}" 0
  CreateShortcut "$SMPROGRAMS\${APP}\${APP} をアンインストール.lnk" "$INSTDIR\Uninstall.exe"

  WriteRegStr HKCU "Software\${APP}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${UNINST}" "DisplayName" "${APP}"
  WriteRegStr HKCU "${UNINST}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINST}" "DisplayIcon" "${LNK_ICON}"
  WriteRegStr HKCU "${UNINST}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${UNINST}" "InstallLocation" "$INSTDIR"
  WriteRegDWORD HKCU "${UNINST}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  RMDir /r "$INSTDIR\company"
  RMDir /r "$INSTDIR\ui"
  RMDir /r "$INSTDIR\agents"
  RMDir /r "$INSTDIR\product"
  RMDir /r "$INSTDIR\python"
  RMDir /r "$INSTDIR\docs"
  Delete "$INSTDIR\app.py"
  Delete "$INSTDIR\company.yaml"
  Delete "$INSTDIR\README.md"
  Delete "$INSTDIR\VERSION"
  Delete "$INSTDIR\Uninstall.exe"
  Delete "$DESKTOP\${APP}.lnk"
  RMDir /r "$SMPROGRAMS\${APP}"
  MessageBox MB_YESNO "モデルと記録（data）も消しますか？$\n$\n$INSTDIR\data" IDNO keep
    RMDir /r "$INSTDIR\data"
  keep:
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "${UNINST}"
  DeleteRegKey HKCU "Software\${APP}"
SectionEnd
