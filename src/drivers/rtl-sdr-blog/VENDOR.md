# Vendored: rtl-sdr-blog

This directory is a vendored copy of the upstream rtl-sdr-blog driver fork, kept in-tree so the
build is reproducible and patchable.

- Upstream: https://github.com/rtlsdrblog/rtl-sdr-blog
- Pinned commit: `aed0ea19f3a273370a13c9009b96313c75d54c7b`
- Pinned date: 2026-03-22
- License: GPL (see `COPYING`)

This fork (not the distro `rtl-sdr` / `librtlsdr-dev` packages) is required for the RTL-SDR Blog
V4 dongle (R828D tuner + RTL2832U).

## How this copy was produced

```
git clone https://github.com/rtlsdrblog/rtl-sdr-blog
git checkout aed0ea19f3a273370a13c9009b96313c75d54c7b
# copied into src/drivers/rtl-sdr-blog/ with the nested .git removed
```

## Local modifications

None. (Record any patches we make to upstream here, with rationale, so they can be re-applied
against future upstream updates.)

## Updating the pin

Re-clone upstream, pick a new commit, replace this tree (keep `VENDOR.md`), update the commit
hash above and the matching entry in `ignore/PROJECT_SUMMARY.md`.
