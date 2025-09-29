```mermaid
graph LR
    controller["controller"]
    summarize["summarize"]
    Rookery["Rookery"]
    OpenSea["OpenSea"]
    classDef controller_loop fill:#fef3c7,stroke:#b45309,stroke-width:1px
    classDef endpoint fill:#e0f2fe,stroke:#0369a1,stroke-width:1px
    class controller controller_loop
    class Rookery endpoint
    class OpenSea endpoint
    controller -->|loop| controller
    summarize -->|egress| Rookery
    OpenSea -->|ingress| controller
    controller --> summarize
```
