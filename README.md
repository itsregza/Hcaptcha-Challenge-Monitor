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

## Config json
```
sitekey - hcaptcha site key eg dd6e16a7-972e-47d2-93d0-96642fb6d8de
webhook - discord webhook for notification
delay - seconds between each challenge refresh. Default 5s
seed_seconds - number of seconds the script will run to detect all possible prompts. After this time all new prompts get sent to webhook
```

Sends discord webhook like the following:


<img width="446" height="461" alt="image" src="https://github.com/user-attachments/assets/57694339-0815-4576-9e99-453d32731d13" />
