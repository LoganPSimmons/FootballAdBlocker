@echo off
rem Football Ad Blocker - install the image model into this copy's own Python.
rem About 5 GB on disk, a 2-3 GB download; run again to retry or to update. Without an NVIDIA GPU
rem see requirements-ml.txt for picking a different PyTorch build first. The app works without the
rem model, on the learned graphics alone, but not as well.
pushd "%~dp0"
echo Installing the image model (PyTorch + open_clip) into "%~dp0python" - this downloads 2-3 GB.
echo.
"python\python.exe" -m pip install --no-warn-script-location -r requirements-ml.txt
if errorlevel 1 (
  echo.
  echo The image model could not be installed. The app still works, on the learned graphics alone.
  echo Check the internet connection, or see requirements-ml.txt for a PyTorch build that matches this machine.
  popd
  pause
  exit /b 1
)
echo.
echo Fetching the model's weights (about 600 MB, once) ...
"python\python.exe" -c "from adblock.detect.scene import SceneModel; SceneModel(); print('Image model ready.')"
popd
if "%~1"=="" pause
exit /b 0
