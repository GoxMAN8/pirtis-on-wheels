# Using the app

The receptionist, live, with made-up customers. One sign-in opens two parts:
- the **caller simulator**, to ring the receptionist as a customer
- the **admin console**, to see what it did, how reliable it is, and to turn bad replies
  into test cases

**Open it:** <https://pirtis-app.bravegrass-b863ec32.germanywestcentral.azurecontainerapps.io>

## Which guide is yours

| You are… | You want to… | Read |
|---|---|---|
| **A tester** | call the receptionist, try to break it, rate calls with scenario cards | [guide-caller.md](guide-caller.md) |
| **A reviewer or admin** | read the gate results, traces and drift, review feedback, save test cases, run the human evaluation | [guide-admin.md](guide-admin.md) |
| **The owner** | add or remove accounts, sign everyone out, run the gate, read logs and alerts | [guide-admin.md § Owner tasks](guide-admin.md#owner-tasks) |

Every account can open both parts: there are no roles. The split is by job, not by
permission.

![The sign-in page](img/sign-in.jpg)

## Signing in

1. Enter the **name** and **password** you were given. Accounts are made by hand. No
   account? Ask whoever sent you the link.
2. Press **Sign in** once. The first visit after a quiet spell takes **40–50 seconds**
   while the app wakes up; the page says so while it waits. After that it answers at
   once.
3. You land on the caller simulator, or on the page your link pointed to. The admin
   console is at `/admin`, and each part links to the other.
4. **Sign out** is at the top right (in the admin console, in the sidebar). A session
   lasts 12 hours.

| What you see | What it means |
|---|---|
| "That name and password don't match…" | a wrong name or password (both are case-sensitive; paste the password), or the account isn't on the app yet |
| "Signing in…", then "Waking the app up…" | the app is starting; wait up to a minute |
| "The app didn't answer within a minute and a half…" | it may not be running; tell the owner |
| sign-in pauses for 15 minutes | five wrong passwords from your network |

## Limits and privacy

- **"Too many requests this minute":** each account has a per-minute allowance; wait a
  few seconds.
- **"Today's budget for model calls is used up":** a shared daily cap, reset at midnight
  UTC.
- **The customers are made up.** Never type your own name, phone or address.
- **Calls are kept 30 days** so they can be reviewed, then deleted. The admin console's
  analytics hold no words and no names.
