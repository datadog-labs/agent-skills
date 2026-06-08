pub const DD_APM_SKILL: &str = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/dd-apm/SKILL.md"));

pub static DD_APM_SUB_SKILLS: &[(&str, &str)] = &[
    ("service-remapping/SKILL.md",            include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/dd-apm/service-remapping/SKILL.md"))),
    ("k8s-ssi/agent-install/SKILL.md",        include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/dd-apm/k8s-ssi/agent-install/SKILL.md"))),
    ("k8s-ssi/enable-ssi/SKILL.md",           include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/dd-apm/k8s-ssi/enable-ssi/SKILL.md"))),
    ("k8s-ssi/verify-ssi/SKILL.md",           include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/dd-apm/k8s-ssi/verify-ssi/SKILL.md"))),
    ("k8s-ssi/troubleshoot-ssi/SKILL.md",     include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/dd-apm/k8s-ssi/troubleshoot-ssi/SKILL.md"))),
    ("k8s-ssi/onboarding-summary/SKILL.md",   include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/dd-apm/k8s-ssi/onboarding-summary/SKILL.md"))),
    ("linux-ssi/agent-install/SKILL.md",      include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/dd-apm/linux-ssi/agent-install/SKILL.md"))),
    ("linux-ssi/enable-ssi/SKILL.md",         include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/dd-apm/linux-ssi/enable-ssi/SKILL.md"))),
    ("linux-ssi/verify-ssi/SKILL.md",         include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/dd-apm/linux-ssi/verify-ssi/SKILL.md"))),
    ("linux-ssi/troubleshoot-ssi/SKILL.md",   include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/dd-apm/linux-ssi/troubleshoot-ssi/SKILL.md"))),
    ("linux-ssi/onboarding-summary/SKILL.md", include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/dd-apm/linux-ssi/onboarding-summary/SKILL.md"))),
];

pub const DD_AUDIT_SKILL: &str = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/dd-audit/SKILL.md"));

pub static DD_AUDIT_SUB_SKILLS: &[(&str, &str)] = &[
    ("ai-activity-audit/SKILL.md",        include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/dd-audit/ai-activity-audit/SKILL.md"))),
    ("compliance-report/SKILL.md",        include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/dd-audit/compliance-report/SKILL.md"))),
    ("cost-spike-investigation/SKILL.md", include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/dd-audit/cost-spike-investigation/SKILL.md"))),
    ("key-compromise/SKILL.md",           include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/dd-audit/key-compromise/SKILL.md"))),
    ("security-investigation/SKILL.md",   include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/dd-audit/security-investigation/SKILL.md"))),
];

pub const DD_BROWSER_SDK_SKILL: &str = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/dd-browser-sdk/SKILL.md"));

pub static DD_BROWSER_SDK_SUB_SKILLS: &[(&str, &str)] = &[
    ("upgrade-v7/SKILL.md", include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/dd-browser-sdk/upgrade-v7/SKILL.md"))),
];

pub const DD_DOCS_SKILL: &str = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/dd-docs/SKILL.md"));

pub static DD_LLMO_SUB_SKILLS: &[(&str, &str)] = &[
    ("llm-obs-eval-bootstrap/SKILL.md",          include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/dd-llmo/llm-obs-eval-bootstrap/SKILL.md"))),
    ("llm-obs-eval-pipeline/SKILL.md",           include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/dd-llmo/llm-obs-eval-pipeline/SKILL.md"))),
    ("llm-obs-experiment-analyzer/SKILL.md",     include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/dd-llmo/llm-obs-experiment-analyzer/SKILL.md"))),
    ("llm-obs-experiment-py-bootstrap/SKILL.md", include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/dd-llmo/llm-obs-experiment-py-bootstrap/SKILL.md"))),
    ("llm-obs-session-classify/SKILL.md",        include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/dd-llmo/llm-obs-session-classify/SKILL.md"))),
    ("llm-obs-trace-rca/SKILL.md",               include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/dd-llmo/llm-obs-trace-rca/SKILL.md"))),
];

pub const DD_LOGS_SKILL: &str = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/dd-logs/SKILL.md"));

pub const DD_MONITORS_SKILL: &str = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/dd-monitors/SKILL.md"));

pub const DD_PUP_SKILL: &str = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/dd-pup/SKILL.md"));

pub static DD_SECURITY_SUB_SKILLS: &[(&str, &str)] = &[
    ("csm/ownership-agent/SKILL.md", include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/dd-security/csm/ownership-agent/SKILL.md"))),
];

pub static DD_SOFTWARE_DELIVERY_SUB_SKILLS: &[(&str, &str)] = &[
    ("triage-flaky-test/SKILL.md", include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/dd-software-delivery/triage-flaky-test/SKILL.md"))),
    ("unblock-pr/SKILL.md",        include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/dd-software-delivery/unblock-pr/SKILL.md"))),
];
