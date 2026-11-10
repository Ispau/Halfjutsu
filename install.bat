@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
title Halfjutsu installer

rem ---------------------------------------------------------------------------
rem  Halfjutsu one-click installer (Windows 10/11, NVIDIA GPU with 16 GB+ VRAM)
rem  Safe to run again: every step skips what is already done, and model
rem  downloads resume where they stopped.
rem ---------------------------------------------------------------------------

set "WANGP_REPO=https://github.com/deepbeepmeep/WanGP"
set "WANGP_COMMIT=bd1ff5903cbc18b46c3cce3b75bd140870b536a2"
set "PY_VERSION=3.11.14"
set "VENV=.venv-wangp"
set "PY=%VENV%\Scripts\python.exe"
set "TORCH=torch==2.10.0 torchvision==0.25.0 torchaudio==2.10.0 --index-url https://download.pytorch.org/whl/cu130"
set "TRITON=triton-windows>=3.6,<3.7"
set "SAGE=https://github.com/woct0rdho/SageAttention/releases/download/v2.2.0-windows.post6/sageattention-2.2.0+cu130torch2.10.0andhigher.post6-cp310-abi3-win_amd64.whl"
set "KERNELS=https://github.com/deepbeepmeep/WGPKernels/releases/download/v1.4.2/wgpkernels-1.4.2%%2Bcu130-py3-none-win_amd64.whl"

echo.
echo  ==============================================================
echo    HALFJUTSU - local character replacement with Viggle-Animate
echo  ==============================================================
echo.
echo  This installs everything into this folder:
echo    - WanGP (the model runtime), pinned to the tested version
echo    - Python %PY_VERSION% + PyTorch 2.10 (CUDA 13) + SageAttention
echo    - the Viggle-Animate weights (about 26 GB)
echo  Plan on 30-90 minutes (mostly the download) and about 60 GB of disk.
echo.

rem --- 1. Hardware check ------------------------------------------------------
echo [1/7] Checking the graphics card...
where nvidia-smi >nul 2>nul
if errorlevel 1 (
  echo.
  echo  ERROR: no NVIDIA driver found. Halfjutsu needs an NVIDIA GPU.
  echo  Install the latest driver from https://www.nvidia.com/drivers and run this again.
  goto :fail
)
set "VRAM="
for /f "usebackq tokens=1,2 delims=," %%a in (`nvidia-smi --query-gpu^=name^,memory.total --format^=csv^,noheader^,nounits`) do (
  if not defined VRAM (
    set "GPU=%%a"
    set "VRAM=%%b"
  )
)
set /a VRAM=%VRAM: =% 2>nul
echo       GPU: %GPU%  (%VRAM% MB)
if %VRAM% LSS 15000 (
  echo.
  echo  ERROR: this card has %VRAM% MB of video memory. Halfjutsu needs 16 GB.
  goto :fail
)
for /f "usebackq tokens=*" %%d in (`nvidia-smi --query-gpu^=driver_version --format^=csv^,noheader`) do set "DRIVER=%%d"
echo       Driver: %DRIVER%  (CUDA 13 needs driver 580 or newer)
for /f "tokens=1 delims=." %%m in ("%DRIVER%") do set /a DRIVER_MAJOR=%%m
if %DRIVER_MAJOR% LSS 580 (
  echo.
  echo  ERROR: the NVIDIA driver is too old for CUDA 13. Update it from https://www.nvidia.com/drivers
  goto :fail
)

rem --- 2. Git ------------------------------------------------------------------
echo [2/7] Checking Git...
where git >nul 2>nul
if errorlevel 1 (
  echo       Git not found: installing it with winget...
  winget install --id Git.Git -e --source winget --accept-source-agreements --silent
  set "PATH=%ProgramFiles%\Git\cmd;%PATH%"
  where git >nul 2>nul
  if errorlevel 1 (
    echo  ERROR: Git could not be installed. Install it from https://git-scm.com and run this again.
    goto :fail
  )
)

rem --- 3. uv (fast Python installer) -------------------------------------------
echo [3/7] Checking uv...
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv >nul 2>nul
if errorlevel 1 (
  echo       Installing uv from https://astral.sh/uv ...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  where uv >nul 2>nul
  if errorlevel 1 (
    echo  ERROR: uv could not be installed. See https://docs.astral.sh/uv/ and run this again.
    goto :fail
  )
)

rem --- 4. WanGP, pinned ----------------------------------------------------------
echo [4/7] Getting WanGP (pinned version %WANGP_COMMIT:~0,12%)...
if not exist "vendor\WanGP\wgp.py" (
  if not exist vendor mkdir vendor
  git clone "%WANGP_REPO%" vendor\WanGP || goto :fail
)
git -C vendor\WanGP fetch --quiet origin
git -C vendor\WanGP -c advice.detachedHead=false checkout --quiet %WANGP_COMMIT% || (
  echo  ERROR: could not switch WanGP to the tested version.
  goto :fail
)

rem --- 5. Python environment -----------------------------------------------------
echo [5/7] Creating the Python environment (downloads about 4 GB the first time)...
if not exist "%PY%" (
  uv venv "%VENV%" --python %PY_VERSION% || goto :fail
)
uv pip install --python "%PY%" %TORCH% || goto :fail
uv pip install --python "%PY%" -r vendor\WanGP\requirements.txt || goto :fail
uv pip install --python "%PY%" "%TRITON%" || goto :fail
uv pip install --python "%PY%" "%SAGE%" || goto :fail
uv pip install --python "%PY%" --no-deps "%KERNELS%" || goto :fail
uv pip install --python "%PY%" -r requirements.txt || goto :fail
"%PY%" -c "import torch; assert torch.cuda.is_available(), 'CUDA not available'; print('      PyTorch', torch.__version__, 'sees', torch.cuda.get_device_name(0))" || (
  echo  ERROR: PyTorch cannot use the GPU. Update the NVIDIA driver and run this again.
  goto :fail
)

rem --- 6. Model licence ----------------------------------------------------------
echo [6/7] Model licence
if exist "data\licence_accepted.txt" (
  echo       Already accepted.
) else (
  echo.
  echo  Viggle-Animate is derived from MiniMax H3 and is licensed under the MiniMax H3
  echo  Community License, which you must read and accept before downloading.
  echo.
  echo  IMPORTANT: that licence is NOT granted in the European Union, the United Kingdom,
  echo  the Republic of Korea or the United States of America. There it authorises neither
  echo  using the model nor using or showing its outputs. Do not continue if you are in one
  echo  of those territories, unless MiniMax has given you a licence.
  echo  Halfjutsu's own code is MIT; the model is not.
  echo.
  echo    Licence: https://huggingface.co/Viggle/Viggle-Animate/blob/main/LICENSE
  echo    WanGP licence:          vendor\WanGP\LICENSE.txt
  echo.
  set "ANSWER="
  set /p "ANSWER=  Type I AGREE to accept the model licence and download it: "
  if /i not "!ANSWER!"=="I AGREE" (
    echo  Not accepted: nothing was downloaded. Run install.bat again when ready.
    goto :fail
  )
  if not exist data mkdir data
  >"data\licence_accepted.txt" echo Accepted on %DATE% %TIME%
)

rem --- 7. Weights ----------------------------------------------------------------
echo [7/7] Downloading the Viggle-Animate weights (about 26 GB, resumable)...
set PYTHONUTF8=1
"%PY%" download_models.py || (
  echo  ERROR: the download did not finish. Run install.bat again: it resumes where it stopped.
  goto :fail
)

rem --- Desktop shortcut ------------------------------------------------------------
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Halfjutsu.lnk'); $s.TargetPath='%~dp0start_halfjutsu.bat'; $s.WorkingDirectory='%~dp0'; $s.Save()" >nul 2>nul

echo.
echo  ==============================================================
echo    Done. Start Halfjutsu with the "Halfjutsu" icon on your desktop
echo    or with start_halfjutsu.bat. It opens http://127.0.0.1:8787
echo  ==============================================================
echo.
pause
exit /b 0

:fail
echo.
echo  Installation stopped. Fix the message above and run install.bat again.
echo  Help: see README.md, section "Troubleshooting".
echo.
pause
exit /b 1
