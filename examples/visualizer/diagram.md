```mermaid
graph LR
    controller["controller"]
    summarize["summarize"]
    OpenSea["OpenSea"]
    Rookery["Rookery"]
    classDef controller_loop fill:#fef3c7,stroke:#b45309,stroke-width:1px
    classDef endpoint fill:#e0f2fe,stroke:#0369a1,stroke-width:1px
    class controller controller_loop
    class OpenSea endpoint
    class Rookery endpoint
    controller --> summarize
    OpenSea -->|ingress| controller
    controller -->|loop| controller
    summarize -->|egress| Rookery
```
