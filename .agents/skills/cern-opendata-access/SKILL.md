---
name: cern-opendata-access
description: Access CERN Open Data files on lxplus via the EOS filesystem or XRootD protocol. Use when reading, copying, or analyzing CMS/ATLAS/LHCb open data stored under /eos/opendata/.
---

# CERN Open Data Access

All CERN Open Data files live on EOS at `/eos/opendata/` and are publicly readable — no authentication needed.
They are accessiable online `https://opendata.cern.ch/`.

## Use this skill when
- The user wants to read or analyze files from the CERN Open Data portal.
- A task references paths under `/eos/opendata/` or URLs with `root://eospublic.cern.ch`.
- The user asks how to access CMS, ATLAS, or LHCb open datasets on lxplus or remotely.

## Instructions

1. Determine whether the task runs interactively on lxplus or in a batch/remote job — this decides which access method to use.
2. For interactive work or quick exploration, use the FUSE-mounted path directly (Option 1).
3. For batch jobs, grid workflows, or remote access from outside CERN, use XRootD (Option 2).
4. When writing analysis code, prefer XRootD URLs so the code works both locally and in batch without modification.
5. Never copy large datasets to local disk unnecessarily — stream via XRootD instead.

## Access methods

### Option 1: Direct Filesystem (FUSE Mount)

EOS is auto-mounted on lxplus; files are accessible as regular paths:

```bash
ls /eos/opendata/cms/
```

**Use when:** interactive exploration, quick checks, small-scale reads, scripting with standard tools (`cp`, `cat`, etc.).

**Avoid when:** batch jobs or heavy parallel I/O — FUSE can be flaky under load.

### Option 2: XRootD Protocol

Access files via the `eospublic.cern.ch` redirector using `root://` URLs:

```python
# ROOT
import ROOT
f = ROOT.TFile.Open("root://eospublic.cern.ch//eos/opendata/cms/<path>.root")

# uproot
import uproot
f = uproot.open("root://eospublic.cern.ch//eos/opendata/cms/<path>.root")
```

Copy a file locally with `xrdcp`:

```bash
xrdcp root://eospublic.cern.ch//eos/opendata/cms/<path>.root ./local_copy.root
```

**Use when:** batch/grid jobs, remote access from outside CERN, streaming large files, or any production workflow.

### Option 3: CERN Open Data Record ID
If the user provides a CERN Open Data record ID (e.g. `record/12350`), the following methods can
be used to resolve it to a file path or URL.
Firstly, install `pip3 install cernopendata-client` to get access to the CERN Open Data API.

Following examples using the record ID `12350`:
1. with `curl`:
```bash
for a in `cernopendata-client get-file-locations --recid 12350`; do curl -O ${a}; done
```

2. with `xrootd`:
```bash
for a in `cernopendata-client get-file-locations --recid 12350`; do xrdcp root://eospublic/`echo ${a} | cut -c 24-`; done
```

## Quick reference

| Method | Path prefix | Best for |
|---|---|---|
| FUSE | `/eos/opendata/...` | Interactive, browsing, small reads |
| XRootD | `root://eospublic.cern.ch//eos/opendata/...` | Batch jobs, remote access, production |

## Additional notes
- Both methods expose the same data; the path after the prefix is identical.
- Stop and ask the user if the target dataset path or experiment (CMS/ATLAS/LHCb) is unclear before proceeding.
