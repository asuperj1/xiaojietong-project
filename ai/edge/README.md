# edge —— 端侧 llama.cpp 离线 demo

- 用途：展示"端侧轻量部署"创新点（离线问答、本地隐私）。在线服务走服务端 Ollama 推理。
- 步骤：微调产物合并 → `llama.cpp` 转 GGUF → `q4_k_m` 量化（约 5~6GB/7B）→ `llama-server` / 桌面 demo 加载。
- 注意：微信小程序总包上限约 20MB，无法真机装载 300MB+ 模型；端侧以桌面 demo 形式答辩展示。
