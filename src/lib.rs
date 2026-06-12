macro_rules! skill {
    ($path:expr) => {
        include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/", $path))
    };
}

pub static DD_APPS_SUB_SKILLS: &[(&str, &str)] = &[
    ("datadog-app/SKILL.md", skill!("dd-apps/datadog-app/SKILL.md")),
];

pub const DD_APM_SKILL: &str = skill!("dd-apm/SKILL.md");

pub static DD_APM_SUB_SKILLS: &[(&str, &str)] = &[
    ("service-remapping/SKILL.md",            skill!("dd-apm/service-remapping/SKILL.md")),
    ("k8s-ssi/agent-install/SKILL.md",        skill!("dd-apm/k8s-ssi/agent-install/SKILL.md")),
    ("k8s-ssi/enable-ssi/SKILL.md",           skill!("dd-apm/k8s-ssi/enable-ssi/SKILL.md")),
    ("k8s-ssi/verify-ssi/SKILL.md",           skill!("dd-apm/k8s-ssi/verify-ssi/SKILL.md")),
    ("k8s-ssi/troubleshoot-ssi/SKILL.md",     skill!("dd-apm/k8s-ssi/troubleshoot-ssi/SKILL.md")),
    ("k8s-ssi/onboarding-summary/SKILL.md",   skill!("dd-apm/k8s-ssi/onboarding-summary/SKILL.md")),
    ("linux-ssi/agent-install/SKILL.md",      skill!("dd-apm/linux-ssi/agent-install/SKILL.md")),
    ("linux-ssi/enable-ssi/SKILL.md",         skill!("dd-apm/linux-ssi/enable-ssi/SKILL.md")),
    ("linux-ssi/verify-ssi/SKILL.md",         skill!("dd-apm/linux-ssi/verify-ssi/SKILL.md")),
    ("linux-ssi/troubleshoot-ssi/SKILL.md",   skill!("dd-apm/linux-ssi/troubleshoot-ssi/SKILL.md")),
    ("linux-ssi/onboarding-summary/SKILL.md", skill!("dd-apm/linux-ssi/onboarding-summary/SKILL.md")),
];

pub const DD_AUDIT_SKILL: &str = skill!("dd-audit/SKILL.md");

pub static DD_AUDIT_SUB_SKILLS: &[(&str, &str)] = &[
    ("ai-activity-audit/SKILL.md",        skill!("dd-audit/ai-activity-audit/SKILL.md")),
    ("compliance-report/SKILL.md",        skill!("dd-audit/compliance-report/SKILL.md")),
    ("cost-spike-investigation/SKILL.md", skill!("dd-audit/cost-spike-investigation/SKILL.md")),
    ("key-compromise/SKILL.md",           skill!("dd-audit/key-compromise/SKILL.md")),
    ("security-investigation/SKILL.md",   skill!("dd-audit/security-investigation/SKILL.md")),
];

pub const DD_BROWSER_SDK_SKILL: &str = skill!("dd-browser-sdk/SKILL.md");

pub static DD_BROWSER_SDK_SUB_SKILLS: &[(&str, &str)] = &[
    ("upgrade-v7/SKILL.md", skill!("dd-browser-sdk/upgrade-v7/SKILL.md")),
];

pub const DD_DOCS_SKILL: &str = skill!("dd-docs/SKILL.md");

pub static DD_LLMO_SUB_SKILLS: &[(&str, &str)] = &[
    ("llm-obs-eval-bootstrap/SKILL.md",          skill!("dd-llmo/llm-obs-eval-bootstrap/SKILL.md")),
    ("llm-obs-eval-pipeline/SKILL.md",           skill!("dd-llmo/llm-obs-eval-pipeline/SKILL.md")),
    ("llm-obs-experiment-analyzer/SKILL.md",     skill!("dd-llmo/llm-obs-experiment-analyzer/SKILL.md")),
    ("llm-obs-experiment-py-bootstrap/SKILL.md", skill!("dd-llmo/llm-obs-experiment-py-bootstrap/SKILL.md")),
    ("llm-obs-session-classify/SKILL.md",        skill!("dd-llmo/llm-obs-session-classify/SKILL.md")),
    ("llm-obs-trace-rca/SKILL.md",               skill!("dd-llmo/llm-obs-trace-rca/SKILL.md")),
];

pub const DD_LOGS_SKILL: &str = skill!("dd-logs/SKILL.md");

pub const DD_MONITORS_SKILL: &str = skill!("dd-monitors/SKILL.md");

pub const DD_PUP_SKILL: &str = skill!("dd-pup/SKILL.md");

pub static DD_SECURITY_SUB_SKILLS: &[(&str, &str)] = &[
    ("csm/ownership-agent/SKILL.md", skill!("dd-security/csm/ownership-agent/SKILL.md")),
];

pub static DD_SOFTWARE_DELIVERY_SUB_SKILLS: &[(&str, &str)] = &[
    ("triage-flaky-test/SKILL.md", skill!("dd-software-delivery/triage-flaky-test/SKILL.md")),
    ("unblock-pr/SKILL.md",        skill!("dd-software-delivery/unblock-pr/SKILL.md")),
];
