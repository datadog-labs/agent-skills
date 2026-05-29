//! Datadog Agent Skills exposed as Rust constants.
//!
//! Each `<NAME>_SKILL` is the root `SKILL.md` content for that skill.
//! Each `<NAME>_SUB_SKILLS` is a slice of `(relative_path, content)` tuples
//! for any nested `SKILL.md` files the skill ships (e.g. `dd-apm` ships
//! `service-remapping/`, `k8s-ssi/*/`, and `linux-ssi/*/`).
//!
//! Consumers like [pup](https://github.com/datadog-labs/pup) read these
//! constants at compile time and write the bytes to disk during install.

pub const DD_APM_SKILL: &str = include_str!("../dd-apm/SKILL.md");

pub static DD_APM_SUB_SKILLS: &[(&str, &str)] = &[
    (
        "service-remapping/SKILL.md",
        include_str!("../dd-apm/service-remapping/SKILL.md"),
    ),
    (
        "k8s-ssi/agent-install/SKILL.md",
        include_str!("../dd-apm/k8s-ssi/agent-install/SKILL.md"),
    ),
    (
        "k8s-ssi/enable-ssi/SKILL.md",
        include_str!("../dd-apm/k8s-ssi/enable-ssi/SKILL.md"),
    ),
    (
        "k8s-ssi/verify-ssi/SKILL.md",
        include_str!("../dd-apm/k8s-ssi/verify-ssi/SKILL.md"),
    ),
    (
        "k8s-ssi/troubleshoot-ssi/SKILL.md",
        include_str!("../dd-apm/k8s-ssi/troubleshoot-ssi/SKILL.md"),
    ),
    (
        "k8s-ssi/onboarding-summary/SKILL.md",
        include_str!("../dd-apm/k8s-ssi/onboarding-summary/SKILL.md"),
    ),
    (
        "linux-ssi/agent-install/SKILL.md",
        include_str!("../dd-apm/linux-ssi/agent-install/SKILL.md"),
    ),
    (
        "linux-ssi/enable-ssi/SKILL.md",
        include_str!("../dd-apm/linux-ssi/enable-ssi/SKILL.md"),
    ),
    (
        "linux-ssi/verify-ssi/SKILL.md",
        include_str!("../dd-apm/linux-ssi/verify-ssi/SKILL.md"),
    ),
    (
        "linux-ssi/troubleshoot-ssi/SKILL.md",
        include_str!("../dd-apm/linux-ssi/troubleshoot-ssi/SKILL.md"),
    ),
    (
        "linux-ssi/onboarding-summary/SKILL.md",
        include_str!("../dd-apm/linux-ssi/onboarding-summary/SKILL.md"),
    ),
];

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dd_apm_skill_has_frontmatter_and_is_non_empty() {
        assert!(
            DD_APM_SKILL.starts_with("---"),
            "expected YAML frontmatter at start of DD_APM_SKILL"
        );
        assert!(!DD_APM_SKILL.trim().is_empty());
    }

    #[test]
    fn dd_apm_sub_skills_are_well_formed() {
        assert_eq!(DD_APM_SUB_SKILLS.len(), 11);
        for (rel, body) in DD_APM_SUB_SKILLS {
            assert!(
                rel.ends_with("/SKILL.md"),
                "unexpected relative path: {rel}"
            );
            assert!(body.starts_with("---"), "{rel} missing YAML frontmatter");
            assert!(!body.trim().is_empty(), "{rel} body is empty");
        }
    }
}
