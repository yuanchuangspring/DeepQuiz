# DeepQuiz

🚀完全由Deepseek-R1开发的AI试题生成应用，根据用户自上传的PDF文档生成练习题，可自定义题数、难度和题型等。Just4Fun😋

### 应用界面

![](figures/home.png)
![](figures/loading.png)
![](figures/example.png)
![](figures/example_answer.jpg)

### 部署教程

#### 后端部署
```bash
# 安装所需库
pip install fastapi uvicorn python-multipart pymupdf requests sqlalchemy

# 启动
uvicorn main:app --reload

# 测试
curl -X POST -F "file=@/path/to/file.pdf" -F
```

#### 前端部署
直接访问src/frontend/index.html即可

### 开发细节
开发过程及技术细节详见：https://mp.weixin.qq.com/s/MfRcGOHIdj-ZckKGNgk7-Q
