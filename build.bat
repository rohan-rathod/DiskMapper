@echo off
REM ---------------------------------------------------------------
REM Build DiskMapper.exe (single file, no Python needed to run it).
REM Requires: pip install pyinstaller
REM ---------------------------------------------------------------
setlocal
cd /d "%~dp0"

echo [1/3] Generating icon...
python tools\make_icon.py || goto :error

echo [2/3] Running self-tests...
python selftest.py >nul || goto :error
python selftest_features.py >nul || goto :error
python selftest_stats.py >nul || goto :error
echo       core + feature + analytics checks passed.

echo [3/3] Building executable...
python -m PyInstaller --noconfirm --onefile --windowed ^
    --name DiskMapper ^
    --icon "%CD%\assets\diskmapper.ico" ^
    --version-file "%CD%\tools\version_info.txt" ^
    --distpath dist --workpath build\work --specpath build ^
    main.py || goto :error

echo.
echo Done. Your shareable app is: dist\DiskMapper.exe
goto :eof

:error
echo.
echo BUILD FAILED - see the output above.
exit /b 1
