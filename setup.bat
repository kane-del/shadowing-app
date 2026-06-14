@echo off
echo ========================================
echo   ShadowClip - セットアップ
echo ========================================
echo.

:: .env ファイルがなければ作成
if not exist .env (
  copy .env.example .env
  echo .env ファイルを作成しました。
  echo APIキーを設定してください (メモ帳が開きます)...
  notepad .env
  pause
)

:: 依存関係インストール
echo 依存関係をインストール中...
python -m pip install -r requirements.txt

echo.
echo ========================================
echo  起動中... ブラウザで開きます
echo  http://localhost:5001
echo ========================================
echo.
start http://localhost:5001
python app.py
