"""PyInstaller 打包入口：隐藏控制台参数直接启动网关。"""
from app.main import main

if __name__ == "__main__":
    main()
