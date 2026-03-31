@echo off
setlocal

REM Launch StockViewer (no console window)
REM Use absolute pythonw.exe path to avoid WindowsApps python shim.

set "ROOT=%~dp0"
"D:\Python\pythonw.exe" "%ROOT%run.py"

endlocal
