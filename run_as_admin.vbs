Set UAC = CreateObject("Shell.Application")
UAC.ShellExecute "pythonw.exe", "\"" & WScript.Arguments(0) & "\"", "", "runas", 1
