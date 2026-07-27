# Hcaptcha Challenge Monitor

Monitors a specific hcaptcha site key for new prompts. This can be useful for people running ai solvers as it notifies them when to update their ai "trained" solver.

## Setup

```bash
pip install -r requirements.txt
copy config.example.json config.json
```

Put your sitekey + webhook in `config.json`. You need Chrome installed.

## Usage

```bash
python monitor.py
```

Records the first few prompts as a baseline, then pings discord on anything new. Some sites may only have 2 prompts but sometimes i've seen up to 4 at once. 

Debugging port defaults to `9331`. Override with `HCAPTCHA_MONITOR_PORT` if you need to.

Sends discord webhook like the following:


<img width="365" height="441" alt="image" src="https://github.com/user-attachments/assets/2af21a29-b7b7-4c57-a264-84350ac8dccc" />
