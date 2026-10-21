' Director Studio: start without a console window (fallback for systems where the
' pythonw shortcut is blocked). Arguments are passed through (e.g. stop, --no-open).
Option Explicit
Dim shell, fso, root, args, i
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
args = ""
For i = 0 To WScript.Arguments.Count - 1
  args = args & " """ & WScript.Arguments(i) & """"
Next
shell.CurrentDirectory = root
shell.Run """" & root & "\runtime\python\pythonw.exe"" -s """ & root & "\director-studio.pyw""" & args, 0, False
