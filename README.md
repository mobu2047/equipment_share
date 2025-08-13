# equipment_share

实验设备共享平台（Python FastAPI + 静态前端），集成本地 Ollama 接口，实现：

- 前端：输入对话框 + 预留多处图片+文字展示位
- 后端：纯 Python（FastAPI），对接 Ollama 本地接口，提供通用对话与设备推荐 API
- 日志：提供类 Winston 风格的结构化 JSON 日志模块

## 目录结构

```
equipment_share/
  backend/
    core/
      config.py
      logger.py
    models/
      schemas.py
    routers/
      chat.py
      equipment.py
    services/
      ollama_client.py
      equipment_selector.py
    main.py
  static/
    index.html
    styles.css
    app.js
  requirements.txt
  LICENSE
  README.md
```

## 运行要求

- 已安装 Python 3.10+
- 本地已安装并运行 Ollama（默认服务地址 `http://localhost:11434`），并已拉取所需模型，例如：

```
ollama pull llama3.1:8b
```

如需其他模型，可通过环境变量覆盖。

## 快速开始（Windows PowerShell）

```
cd C:\Users\tiany\Desktop\equipment_share
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

setx OLLAMA_MODEL "llama3.1:8b"
setx OLLAMA_HOST "http://localhost:11434"

uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

启动后访问：`http://localhost:8000`。

## 环境变量

- `OLLAMA_HOST`：Ollama 服务地址（默认 `http://localhost:11434`）
- `OLLAMA_MODEL`：默认模型名称（默认 `llama3.1:8b`）
- `API_HOST`：后端监听地址（默认 `0.0.0.0`）
- `API_PORT`：后端端口（默认 `8000`）

## API 概览

- `POST /api/chat`：通用对话
  - 请求：`{ "prompt": "..." }`
  - 响应：`{ "reply": "..." }`

- `POST /api/recommend`：根据实验描述推荐设备
  - 请求：`{ "experiment": "..." }`
  - 响应：
    ```json
    {
      "items": [
        {
          "name": "顯微鏡A",
          "description": "...",
          "score": 0.92,
          "tags": ["microscopy"],
          "image_url": "/static/assets/placeholder1.png"
        }
      ]
    }
    ```

## 日志

后端使用 `backend/core/logger.py` 提供的结构化日志，支持 `debug/info/warning/error` 等级，输出 JSON，便于收集与检索。

## 前端

静态文件位于 `static/`，包括：

- `index.html`：输入框/展示区
- `styles.css`：样式
- `app.js`：与后端交互逻辑

页面包含 3 个图片+文字预留位，可按需在 `app.js` 中绑定后端返回结果进行填充。

## 开发

推荐使用 `uvicorn --reload` 热重载开发；如需跨域访问，可在 `backend/main.py` 内配置 CORS。

## 许可证

Apache-2.0，详见 `LICENSE`。
