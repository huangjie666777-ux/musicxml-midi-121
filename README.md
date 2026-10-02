# MusicXML → MIDI 转换后端

未压缩 score-partwise MusicXML 转 MIDI Type 1（960 PPQ）的纯后端服务，供排练使用。

## 运行

```bash
.venv/bin/python -m uvicorn app.main:app --port 8123
```

## 接口

- `POST /convert`：multipart 上传 `.musicxml` 文件。成功返回
  `{token, download_url, summary}`（summary 含各声部、音符数、总拍数）；
  失败返回 422/413 并定位分谱、小节、音符，不留下任何成品。
- `GET /download/{token}`：下载转换成功的 MIDI 文件。

## 模块

- `app/parser.py`：安全解析（禁外部实体/联网 DTD、命名空间兼容、
  上传与音符数量限制）、duration/divisions 计时、chord/backup/forward 游标、
  非法结构/音高/时值定位报错，拒绝装饰音、打击乐、移调、反复记号。
- `app/convert.py`：跨 part 小节列对齐（每列取最大时值，支持不完整首小节）、
  tie 链合并与校验、首 part 速度提取（默认 120 BPM）、15 声部上限。
- `app/midi.py`：Type 1、960 PPQ、每 part+voice 一轨、非打击乐通道、
  绝对时间四舍五入为非负 delta、同 tick 先 note_off 后 note_on。
- `app/main.py`：FastAPI 接口与结果暂存（仅成功结果可下载）。

## 测试

```bash
.venv/bin/python -m pytest tests -q
```

