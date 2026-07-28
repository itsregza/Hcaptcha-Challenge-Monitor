# Hcaptcha Challenge Monitor

Monitors a specific hcaptcha site key for new prompts. This can be useful for people running ai solvers as it notifies them when to update their ai "trained" solver.

## Setup

```bash
pip install -r requirements.txt
```

Put your sitekey + webhook in `config.json`. You need Chrome installed.

## Usage

```bash
python monitor.py
```

Opens a small captcha window (same idea as a solver window). The challenge should pop on its own — hit **Open challenge** if it stalls.

Records the first few prompts as a baseline, then pings discord on anything new. Some sites may only have 2 prompts but sometimes i've seen up to 4 at once.

Debugging port defaults to `9331`. Override with `HCAPTCHA_MONITOR_PORT` if you need to.

Sends discord webhook like the following:


<img width="446" height="461" alt="image" src="https://github.com/user-attachments/assets/57694339-0815-4576-9e99-453d32731d13" />
