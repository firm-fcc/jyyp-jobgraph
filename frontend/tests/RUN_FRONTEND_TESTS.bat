@echo off
setlocal
cd /d "%~dp0"
if not exist node_modules (
  echo [1/2] Installing frontend test dependencies...
  call npm install
  if errorlevel 1 exit /b 1
)
echo [2/2] Running unit tests and coverage...
call npm run test:coverage
exit /b %errorlevel%
