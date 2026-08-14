# Technology Documents for IntelliKnow Testing

Upload the `.docx` and `.pdf` files in this directory through the IntelliKnow Documents page. The `source/` directory contains the official NVIDIA pages downloaded on 2026-08-12 and is retained for traceability; do not upload the HTML source files.

## Before Uploading

Create an intent in the IntelliKnow Intent Management page with these values:

- **Name:** Technology
- **Slug:** `technology`
- **Description:** GPU and storage infrastructure, distributed systems, NVIDIA Dynamo, NCCL, CUDA, NVMe, AI inference, networking, collective communication, and performance engineering documentation.
- **Keywords:** NVIDIA, Dynamo, NCCL, CUDA, GPU, NVMe, NVM Express, SSD, inference, KV cache, collective communication, all-reduce, NVLink, InfiniBand

Classification is fail-fast. If the configured LLM is unavailable or cannot classify a document confidently, IntelliKnow will reject the upload so you can retry instead of silently placing it in the wrong intent.

## Upload-Ready Documents

| Document | Contents | Suggested questions |
| --- | --- | --- |
| `NVIDIA_Dynamo_Quickstart.docx` | Local Dynamo quickstart | How do I start a Dynamo frontend and worker locally?; How do I verify the endpoint? |
| `NVIDIA_Dynamo_Architecture_and_Routing.docx` | Overall architecture, KV-aware routing, and disaggregated serving | What are Dynamo's three planes?; When should Dynamo use disaggregated serving?; What routing options are available on Kubernetes? |
| `NVIDIA_NCCL_Usage_and_Collectives.docx` | Basic use and collective operations | What is an NCCL communicator?; What is the difference between AllReduce and ReduceScatter? |
| `NVIDIA_NCCL_Environment_Variables.docx` | System, networking, debugging, and performance configuration | What does NCCL_SOCKET_IFNAME control?; How do I enable NCCL debug logs? |
| `NVIDIA_NCCL_Troubleshooting.docx` | GPU, networking, runtime, MPI, performance, logging, and RAS diagnostics | How should I troubleshoot NCCL networking?; Which NCCL logging settings help diagnose a hang? |
| `NVM_Express_Revision_1.3.pdf` | NVM Express Base Specification Revision 1.3 | What is an NVMe submission queue?; Which admin commands are mandatory?; How does NVMe namespace management work? |

## Official Sources

- [NVIDIA Dynamo Quickstart](https://docs.nvidia.com/dynamo/dev/cli/getting-started/quickstart)
- [NVIDIA Dynamo Overall Architecture](https://docs.nvidia.com/dynamo/dev/knowledge-base/overview)
- [NVIDIA Dynamo KV-Aware Routing](https://docs.nvidia.com/dynamo/dev/kubernetes/kv-aware-routing/overview)
- [NVIDIA Dynamo Disaggregated Serving](https://docs.nvidia.com/dynamo/dev/kubernetes/disaggregated-serving/overview)
- [NVIDIA NCCL User Guide: Using NCCL](https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/usage.html)
- [NVIDIA NCCL User Guide: Collective Operations](https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/usage/collectives.html)
- [NVIDIA NCCL User Guide: Environment Variables](https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/env.html)
- [NVIDIA NCCL User Guide: Troubleshooting](https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/troubleshooting.html)

The NVMe Base Specification Revision 1.3 PDF was supplied locally as `/Users/aaron/Downloads/NVM_Express_Revision_1.3.pdf`. The copy in this directory is byte-for-byte identical to that source (SHA-256: `4cd8b3f8d434b30a4418c23e94f8a433a1366fa265498334ead5620c33c8a5b1`).
