# User guide

This guide is for people whose accounts are managed by NA-SSO. Your
administrator creates your account and decides which systems (targets) you get.
NA-SSO is where you **take over your own password**, manage your **SSH keys**,
download your **OpenVPN** profile, and check the status of each system you have
access to.

NA-SSO is not a login gateway. You still sign in to GitLab, Nextcloud, the
firewall, an SSH host, and so on **directly**, with the username and password
you set here. NA-SSO just keeps those local accounts in sync.

## Your first sign-in (important)

Your administrator gives you a **username** and a **temporary password**. That
temporary password works **only on NA-SSO** — not on any of the actual systems
yet.

1. Open the NA-SSO address your administrator gave you and sign in with the
   temporary password.
2. You will be asked to set your **own** password.
3. As soon as you do, NA-SSO creates or enables your accounts on every system
   you've been assigned and sets that password on them.

Until you complete this step, your assigned systems show **CHPW** ("change
password") and your accounts there stay inactive. This is deliberate: your
temporary password is never sent to those systems, so nobody can use it to log
in anywhere but NA-SSO.

If your administrator ever resets your password, you return to this same
step — set a new password to reactivate your accounts.

## Your account page

Everything you control lives on your **Account** page.

### Password

Change your password at any time under **Account password**. Your new password
propagates to every system you're assigned to, so afterwards you use the same
password everywhere NA-SSO manages you.

**Password expiry.** If your organization sets an expiry, your Account page
shows the date. When a password expires you'll be asked to either **change it**
or, if policy allows, **acknowledge** keeping it for now. The screen tells you
exactly what will happen (a new expiry date, or how long a grace period lasts)
before you confirm.

### Assigned targets

The **Assigned targets** section lists every system you have access to and its
current state — active, still in **CHPW**, or reporting a problem your
administrator needs to fix. "Local account only" means that entry exists just on
NA-SSO and isn't propagated anywhere.

### SSH keys

If you're assigned an SSH host, manage your keys under **SSH keys**. You can
keep a **separate named key per device** (laptop, workstation, phone), so losing
one device never means rotating all of them.

- **Add a key.** Your browser generates the key pair locally and only your
  **public** key is stored. Give it a name and an optional expiry date.
- **Rotate.** NA-SSO adds the new key to the host **before** revoking the old
  one, so you're never locked out mid-rotation.
- **Revoke one key**, or let one expire automatically.
- **Emergency removal.** "Revoke all SSH keys" removes every key at once and
  asks for your current password to confirm.

Note: current SSH hosts can't report a key's last-use time, so the page won't
show one.

### OpenVPN access

If you're assigned an OPNsense firewall with OpenVPN self-service enabled, you
can **download your own `.ovpn` profile** from the **OpenVPN profiles** section.
When the server requires a certificate, the download bundles an
OPNsense-issued client certificate (and its private key) for you.

The profile is streamed to you **once** and is not stored by NA-SSO — save it
somewhere safe. If you leave or your access is removed, your administrator
revokes the certificate, which invalidates the profile.

## Multi-factor authentication (operators)

If you're an **operator** (you administer other users, targets, or audit),
your organization may require MFA. From **My account** you can enrol:

- a **passkey** (WebAuthn security key or platform authenticator), or
- **TOTP** (a six-digit code from an authenticator app).

You'll also get one-time **recovery codes** — save them somewhere safe; each
works once if you lose your other factor. Enrolling, revoking, or replacing a
factor asks for your current password again. Ordinary managed users who only use
their assigned systems don't need this.

## Getting help

If an assigned system shows a problem you can't clear yourself, use the support
contact your administrator configured (shown in the app where relevant).

**Never send anyone your password or a private key** — no NA-SSO workflow ever
asks you to. NA-SSO only stores your **public** SSH keys, and your passwords are
never kept in readable form.
