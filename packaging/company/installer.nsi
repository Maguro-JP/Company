; Company の Setup.exe。build.sh から makensis で組む（SRC、ICON、OUT、VERSION を渡す）
;
; 置き場（company/paths.py と同じ。自作の AdaptiveAIStudio・CharacterDesigner にそろえる）
;   C:\Program Files\Company\      プログラム（入れ直すたびに丸ごと置き換える。書き換えない）
;   C:\ProgramData\Company\        作業場（company.yaml、agents、product、docs、data）。AI が書き換え、更新しても残す
;   C:\LLM\gguf                    模型（自作アプリで共有。消さない）
;
; 入れる: 前の版（v0.1.x、%LOCALAPPDATA%\Programs\Company）があれば、作業場と記録を移し、模型を C:\LLM\gguf へ移してから、前の版を消す
; 更新: 同じ場所に入れ直す。前のプログラムを消してから置くので、古いファイルが残らない。作業場と模型はそのまま
; 消す: プログラム、ショートカット、登録を消す。作業場（ProgramData）は聞いてから消す。模型（C:\LLM）は消さない
Target amd64-unicode
Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"

!define APP "Company"
!define UNINST "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP}"

Name "${APP}"
OutFile "${OUT}"
InstallDir "$PROGRAMFILES64\${APP}"
InstallDirRegKey HKLM "Software\${APP}" "InstallDir"
RequestExecutionLevel admin
SetCompressor /SOLID lzma

!define MUI_ICON "${ICON}"
!define MUI_UNICON "${ICON}"
!define LNK_ICON "$INSTDIR\ui\icon\app.ico"

!define MUI_FINISHPAGE_RUN "$INSTDIR\python\pythonw.exe"
!define MUI_FINISHPAGE_RUN_PARAMETERS "-E -s app.py"
!define MUI_FINISHPAGE_RUN_TEXT "Company を起動する"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Japanese"

Var OLD

Section "Install"
  ; 更新のとき、前のプログラムを先に消す（消したファイルが残って古いまま読まれないように）
  RMDir /r "$INSTDIR\company"
  RMDir /r "$INSTDIR\ui"
  RMDir /r "$INSTDIR\agents"
  RMDir /r "$INSTDIR\product"
  RMDir /r "$INSTDIR\docs"
  RMDir /r "$INSTDIR\python"
  SetOutPath "$INSTDIR"
  File /r "${SRC}\*.*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"

  ; 作業場。ふつうの利用者も書けるようにする（ProgramData は既定で管理者しか書けない）。
  ; S-1-5-32-545 は Users 群の番号。日本語の Windows では名前が「ユーザー」になるので、名前ではなく番号で指す
  CreateDirectory "$COMMONPROGRAMDATA\${APP}"
  nsExec::ExecToLog '"$SYSDIR\icacls.exe" "$COMMONPROGRAMDATA\${APP}" /grant *S-1-5-32-545:(OI)(CI)M /T /Q'
  ; 自社のツールどうしの登録簿（services.json）。どのアプリも書けるように Users に書き込みを付ける
  CreateDirectory "$COMMONPROGRAMDATA\Maguro"
  nsExec::ExecToLog '"$SYSDIR\icacls.exe" "$COMMONPROGRAMDATA\Maguro" /grant *S-1-5-32-545:(OI)(CI)M /T /Q'
  ; 模型の置き場はシステムのドライブの LLM（company/paths.py の models_dir と同じ。C: 以外にも合わせる）
  StrCpy $1 "$WINDIR" 3
  CreateDirectory "$1LLM\gguf"
  CreateDirectory "$1LLM\huggingface"
  nsExec::ExecToLog '"$SYSDIR\icacls.exe" "$1LLM" /grant *S-1-5-32-545:(OI)(CI)M /Q'

  ; 作業場を整える。前の版（利用者ごとに入れていた v0.1.x）があれば、そこから移す
  SetShellVarContext current
  ReadRegStr $OLD HKCU "${UNINST}" "InstallLocation"
  StrCmp $OLD "" 0 +2
    StrCpy $OLD "$LOCALAPPDATA\Programs\${APP}"
  IfFileExists "$OLD\app.py" 0 no_old
    DetailPrint "前の版を移しています: $OLD"
    nsExec::ExecToLog '"$INSTDIR\python\python.exe" -m company.migrate "$OLD"'
    Pop $0
    StrCmp $0 "0" 0 migrate_failed
      RMDir /r "$OLD"
      Delete "$SMPROGRAMS\${APP}\*.lnk"
      RMDir "$SMPROGRAMS\${APP}"
      Delete "$DESKTOP\${APP}.lnk"
      DeleteRegKey HKCU "${UNINST}"
      DeleteRegKey HKCU "Software\${APP}"
      Goto no_old
    migrate_failed:
      MessageBox MB_OK|MB_ICONEXCLAMATION "前の版の一部を移せなかったので、前の版はそのまま残しました。$\r$\n記録: $COMMONPROGRAMDATA\${APP}\migrate.log" /SD IDOK
  no_old:
  nsExec::ExecToLog '"$INSTDIR\python\python.exe" -m company.migrate'

  SetShellVarContext all
  SetOutPath "$INSTDIR"
  CreateDirectory "$SMPROGRAMS\${APP}"
  CreateShortcut "$SMPROGRAMS\${APP}\${APP}.lnk" "$INSTDIR\python\pythonw.exe" "-E -s app.py" "${LNK_ICON}" 0
  CreateShortcut "$SMPROGRAMS\${APP}\${APP} をアンインストール.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\${APP}.lnk" "$INSTDIR\python\pythonw.exe" "-E -s app.py" "${LNK_ICON}" 0

  WriteRegStr HKLM "Software\${APP}" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "${UNINST}" "DisplayName" "${APP}"
  WriteRegStr HKLM "${UNINST}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKLM "${UNINST}" "DisplayIcon" "${LNK_ICON}"
  WriteRegStr HKLM "${UNINST}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${UNINST}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKLM "${UNINST}" "NoModify" 1
  WriteRegDWORD HKLM "${UNINST}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  ; 入れた所は丸ごと消す（__pycache__ など起動で出来た物も残さない）。名前が違う所に入れたときは、入れた物だけを消す
  RMDir /r "$INSTDIR\company"
  RMDir /r "$INSTDIR\ui"
  RMDir /r "$INSTDIR\agents"
  RMDir /r "$INSTDIR\product"
  RMDir /r "$INSTDIR\docs"
  RMDir /r "$INSTDIR\python"
  Delete "$INSTDIR\app.py"
  Delete "$INSTDIR\company.yaml"
  Delete "$INSTDIR\README.md"
  Delete "$INSTDIR\VERSION"
  Delete "$INSTDIR\Uninstall.exe"
  ${If} ${FileExists} "$INSTDIR\*.*"
  ${AndIf} "$INSTDIR" == "$PROGRAMFILES64\${APP}"
    RMDir /r "$INSTDIR"
  ${EndIf}
  RMDir "$INSTDIR"
  SetShellVarContext all
  Delete "$SMPROGRAMS\${APP}\*.lnk"
  RMDir /r "$SMPROGRAMS\${APP}"
  Delete "$DESKTOP\${APP}.lnk"
  DeleteRegKey HKLM "${UNINST}"
  DeleteRegKey HKLM "Software\${APP}"
  ; 取った更新の Setup.exe は、作業場を残すときも消す（作業の物ではなく一時の物）
  RMDir /r "$COMMONPROGRAMDATA\${APP}\updates"
  ; 作業場（AI が作ったプロダクトと記録）は聞いてから消す。黙って消す（/S）ときは残す。模型（C:\LLM）はどのアプリも消さない
  MessageBox MB_YESNO|MB_ICONQUESTION "作業場（$COMMONPROGRAMDATA\${APP}）も消しますか？$\r$\nAI が作ったプロダクト、記録、設定が入っています。模型（C:\LLM）はここには入っていません。" /SD IDNO IDNO keep
    RMDir /r "$COMMONPROGRAMDATA\${APP}"
  keep:
SectionEnd
