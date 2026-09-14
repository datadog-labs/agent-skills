#!/usr/bin/env python3
"""Compositional router over catalog.json (plan phase P3).

Turns a recommended product list plus DETECTED context (platform, cloud) into an
ordered onboarding plan by resolving category-level `requires` against the
capability graph and topologically ordering the bound dependency edges. `kind`
is only the deterministic tie-breaker for otherwise-independent nodes. No
intent-to-skill table exists anywhere: intents live only in the recommender;
here we compose.

CLI:
  python3 resolve.py --products "APM,Infrastructure Monitoring" --platform kubernetes --cloud aws
"""

import argparse
import difflib
import json
import os
import re
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))          # …/dd-orchestrator/scripts
SKILL = os.path.dirname(HERE)                              # …/dd-orchestrator
CATALOG = os.path.join(SKILL, "catalog.json")

KIND_RANK = {"foundation": 0, "cloud-connect": 1, "platform-install": 2,
             "product-enable": 3, "verify-troubleshoot": 4, "lifecycle": 5}

# recommender product name / alias -> catalog product token
PRODUCT_TOKENS = {
    "apm": "apm", "application performance monitoring": "apm",
    "llm observability": "llm-obs", "llmo": "llm-obs", "llm obs": "llm-obs",
    "rum": "rum", "real user monitoring": "rum", "session replay": "rum",
    "error tracking": "error-tracking", "product analytics": "product-analytics",
    "infrastructure monitoring": "infra", "infra": "infra", "container monitoring": "infra",
    "log management": "logs", "logs": "logs",
    "database monitoring": "dbm", "dbm": "dbm",
    "cloud cost management": "cost", "ccm": "cost", "cloud cost": "cost",
    "cloud security": "cloud-security", "csm": "cloud-security", "cspm": "cloud-security", "cws": "cloud-security",
    "cloud siem": "cloud-siem",
    "app and api protection": "aap", "aap": "aap", "asm": "aap",
    "test optimization": "test-optimization", "ci visibility": "test-optimization",
    "code coverage": "code-coverage",
    "synthetic monitoring": "synthetics", "synthetics": "synthetics",
    "continuous profiler": "profiler", "profiler": "profiler",
    "network device monitoring": "ndm", "sensitive data scanner": "sds",
    "slo": "slo", "monitors": "monitors", "opentelemetry": "otel", "otel": "otel",
    "studio": "studio", "storage": "storage", "agent observability": "agent-observability",
    "mobile rum": "rum-mobile", "rum mobile": "rum-mobile", "mobile monitoring": "rum-mobile",
    "datadog app": "apps", "datadog apps": "apps", "dashboards app": "apps",
}

# detected platform sub-variants -> the catalog platform token the Agent skills use.
# 'fargate' is deliberately NOT aliased: EKS-Fargate is kubernetes but ECS-Fargate is ecs, so
# collapsing it would pick the wrong installer (ROB-7); leave it to a dead-end/choice instead.
PLATFORM_ALIASES = {
    "k8s": "kubernetes", "eks": "kubernetes", "gke": "kubernetes", "aks": "kubernetes",
    "openshift": "kubernetes", "oke": "kubernetes", "tkg": "kubernetes", "rancher": "kubernetes",
    "autopilot": "kubernetes",
    # Generic hosts/VMs do not imply Linux, and EC2/GCE do not reveal the guest
    # operating system. GCR is a registry, not the Cloud Run compute platform.
    "cloudrun": "cloud-run",
    # Serverless/PaaS deploy targets have no Agent installer; collapse the common ones to a
    # single 'serverless' class so Agent-based products dead-end honestly (with a pointer)
    # instead of offering an install choice that cannot match the context. (spec req #12)
    "vercel": "serverless", "netlify": "serverless",
}
CLOUD_ALIASES = {
    "amazon": "aws", "amazon web services": "aws", "google": "gcp", "google cloud": "gcp",
    "microsoft azure": "azure", "az": "azure",
}


def normalize_platform(p):
    # ROB-2: lowercase the TOKEN itself (not just the lookup key), so canonical-but-
    # capitalized inputs like "Kubernetes"/"Docker" resolve instead of dead-ending.
    p2 = (p or "").strip().lower()
    return PLATFORM_ALIASES.get(p2, p2)


def normalize_cloud(c):
    c2 = (c or "").strip().lower()
    return CLOUD_ALIASES.get(c2, c2)


# Serverless/PaaS platforms Datadog cannot instrument with an Agent installer. A product
# that needs a platform-install dead-ends here (with a pointer), never as a choice. (req #12)
SERVERLESS_PLATFORMS = {"serverless"}


def _serverless_pointer(platform):
    """Pointer appended to a platform dead-end when the platform is serverless/PaaS
    (no Agent installer exists). Empty string for every other platform."""
    if platform in SERVERLESS_PLATFORMS:
        return (" — serverless/PaaS has no Agent installer; use the agentless integration "
                "(e.g. the Vercel/Netlify <-> Datadog integration) or an agentless SDK, "
                "see docs.datadoghq.com")
    return ""


def _cloud_suffix(cloud):
    """Shared ' for cloud X' clause appended to a platform dead-end message, or ''."""
    return f" for cloud '{_san(cloud)}'" if cloud and cloud != "none" else ""


def _san(s, n=80):
    """ROB-8: sanitize free-text before echoing it into dead-end/choice strings."""
    s = "".join(ch if (ch >= " " and ch != "\x7f") else " " for ch in str(s))
    s = " ".join(s.split())
    return (s[:n] + "…") if len(s) > n else s


def load_catalog(path=CATALOG):
    with open(path) as f:
        return json.load(f)


def canonical_key(node, by_key):
    """Walk `duplicate_of` links to the canonical (non-duplicate) root key.

    `by_key` maps every node's `key` to the node itself. Guards against cycles;
    malformed catalogs that would cycle are rejected by check_catalog before
    this ever runs on real data.
    """
    key, seen = node["key"], set()
    while by_key.get(key, {}).get("duplicate_of"):
        if key in seen:
            break
        seen.add(key)
        key = by_key[key]["duplicate_of"]
    return key


def display_name(token):
    """First human alias in PRODUCT_TOKENS that maps to `token` (else the token itself)."""
    return next((k for k, v in PRODUCT_TOKENS.items() if v == token), token)


def normalize_product(name, catalog_tokens=None):
    """Map a product name/alias to its catalog token, or None if unrecognized.

    Accepts the recommender names/aliases in PRODUCT_TOKENS and, when a set of
    catalog product tokens is supplied, every such token as an alias for itself
    (so an operator can pass the exact token printed in catalog.json). Case- and
    whitespace-insensitive.
    """
    key = str(name).strip().lower()
    if key in PRODUCT_TOKENS:
        return PRODUCT_TOKENS[key]
    if catalog_tokens and key in catalog_tokens:
        return key
    return None


# Orchestrator machinery, not user-facing products. Excluded from intent detection so
# "recommend what I need" (the canonical vague case) is never read as a named product.
_NON_PRODUCT_TOKENS = frozenset({"account", "orchestrate", "recommend"})


def detect_products(intent, catalog_tokens=None):
    """Scan a free-text intent for EXPLICITLY NAMED products; return their catalog tokens
    ordered by first mention. Empty result => no product named => the caller falls back to
    the recommender (spec req "Ability to get a shortcut and skip some steps").

    Matches product NAMES, not the concepts they cover (word-boundary, longest-phrase-first):
    "Agent Observability" (a name) matches; "track LLM model calls" (a description) does not.
    The vocabulary is exactly what --products accepts (PRODUCT_TOKENS aliases + the catalog's
    own tokens), minus orchestrator machinery — so it never drifts from the resolver.
    """
    if not intent:
        return []
    vocab = (set(PRODUCT_TOKENS) | set(catalog_tokens or [])) - _NON_PRODUCT_TOKENS
    # Longest phrase first so "mobile rum" wins over "rum"; the matched span is then consumed
    # so a contained shorter alias cannot double-match. Fully ordered key => deterministic.
    terms = sorted(vocab, key=lambda t: (-t.count(" "), -len(t), t))
    original = intent.lower()
    haystack = original
    first_pos = {}
    for term in terms:
        pattern = r"\b" + r"\s+".join(re.escape(w) for w in term.split()) + r"\b"
        if not re.search(pattern, haystack):
            continue
        token = normalize_product(term, catalog_tokens)
        if token:
            here = re.search(pattern, original)
            pos = here.start() if here else 0
            first_pos[token] = min(pos, first_pos.get(token, pos))
        haystack = re.sub(pattern, " ", haystack)   # consume so a shorter alias can't re-match
    return [t for t, _ in sorted(first_pos.items(), key=lambda kv: (kv[1], kv[0]))]


class Router:
    def __init__(self, catalog, enabled_only=True):
        # A canonical node identifies a capability; it is not necessarily the
        # implementation available at runtime. Choose one implementation per duplicate
        # group, preferring the canonical when it is enabled and otherwise falling back
        # to a stable enabled duplicate. This prevents a disabled capability identity
        # from hiding a genuinely equivalent enabled implementation.
        nodes = [n for n in catalog["nodes"] if n["kind"] != "internal"]
        by_key = {n["key"]: n for n in nodes}
        # Every catalog product token is a first-class --products alias for itself, so an
        # operator can pass the exact token printed in catalog.json (spec req #11). Derived
        # from the catalog so it stays in sync as the catalog evolves.
        self.product_tokens = frozenset(
            n["product"].strip().lower() for n in nodes if n.get("product")
        )

        groups = {}
        for n in nodes:
            groups.setdefault(canonical_key(n, by_key), []).append(n)

        self.live = []
        self.alias_ids_by_key = {}
        self.by_id = {}
        for root, implementations in groups.items():
            canonical = by_key.get(root)
            if enabled_only:
                available = [n for n in implementations if n.get("enabled", True)]
                if not available:
                    continue
                chosen = canonical if canonical in available else min(available, key=lambda n: (n["id"], n["key"]))
            else:
                chosen = canonical or min(implementations, key=lambda n: (n["id"], n["key"]))
            self.live.append(chosen)
            aliases = {n["id"] for n in implementations}
            self.alias_ids_by_key[chosen["key"]] = aliases
            for alias in aliases:
                self.by_id[alias] = chosen

        self.account = next((n for n in self.live if n["product"] == "account"), None)
        self.install = {}
        self.cloud = {}
        self.enable = {}
        self.verify = {}
        self.triggered = {}
        self.installer_products = set()
        for n in self.live:
            if n["kind"] == "platform-install":
                for platform in n["platform"]:
                    self.install.setdefault(platform, []).append(n)
                self.installer_products.update(n.get("delivers", []))
            if n["kind"] == "cloud-connect" and n["action"] == "connect":
                for cloud in n["cloud"]:
                    self.cloud.setdefault(cloud, []).append(n)
            if n["kind"] == "product-enable":
                self.enable.setdefault(n["product"], []).append(n)
            if n["kind"] == "verify-troubleshoot" and "verify" in n["action"]:
                if n["product"]:
                    self.verify.setdefault(n["product"], []).append(n)
            for product in n.get("trigger_products") or []:
                self.triggered.setdefault(product, []).append(n)

        for index in (self.install, self.cloud, self.enable, self.verify, self.triggered):
            for values in index.values():
                values.sort(key=lambda n: (n["id"], n["key"]))

    @staticmethod
    def _facet_rank(node):
        """Rank a matching node by constraint specificity, not list length."""
        constrained = int(bool(node["platform"])) + int(bool(node["cloud"]))
        return (constrained, -len(node["platform"]), -len(node["cloud"]))

    @staticmethod
    def _first(candidates):
        return candidates[0] if candidates else None

    def _bind_soft(self, cat, ctx, fixed_cloud=None):
        """Bind a suggests edge; returns None (silently) if it cannot bind."""
        if cat == "cloud-connect":
            c = fixed_cloud or ctx.get("cloud")
            return self._first(self.cloud.get(c, [])) if c and c != "none" else None
        if cat == "platform-install":
            p = ctx.get("platform")
            candidates = [n for n in self.install.get(p, []) if self._fits(n, ctx)]
            return self._first(candidates) if p and p != "none" else None
        if cat == "foundation":
            return self.account
        return None

    def _match(self, cands, ctx):
        """Pick the candidate whose platform/cloud facets fit the detected context."""
        best, best_score = None, None
        for n in cands:
            if n["platform"] and ctx.get("platform") not in n["platform"]:
                continue
            if n["cloud"] and ctx.get("cloud") not in n["cloud"]:
                continue
            score = self._facet_rank(n)
            if best_score is None or score > best_score:
                best, best_score = n, score
        return best

    @staticmethod
    def _fits(node, ctx):
        return ((not node["platform"] or ctx.get("platform") in node["platform"]) and
                (not node["cloud"] or ctx.get("cloud") in node["cloud"]))

    def _nearest_product(self, name):
        """Nearest known product for an unrecognized input: (display, token), or None.

        Searches recommender names/aliases plus catalog tokens and returns the closest
        only when it is a genuine near-miss (difflib cutoff 0.6): a typo like
        "llmobs" -> "llm-obs" gets a "did you mean" hint, while garbage with no real
        similarity returns None so the dead-end carries no fabricated suggestion.
        """
        vocab = list(PRODUCT_TOKENS) + sorted(self.product_tokens)
        match = difflib.get_close_matches(str(name).strip().lower(), vocab, n=1, cutoff=0.6)
        if not match:
            return None
        token = normalize_product(match[0], self.product_tokens)
        return display_name(token), token

    def resolve(self, products, ctx, selections=None):
        """Resolve products for context.

        ``selections`` maps a product token/name (or a returned choice ``need``) to
        a skill id. It lets callers resolve a genuine capability choice without
        relying on catalog order, e.g. ``{"agent-observability": "trace-rca"}``.
        For convenience, the same mapping may be supplied as ``ctx["selections"]``.
        """
        raw_selections = dict(ctx.get("selections") or {})
        raw_selections.update(selections or {})
        selected = {}
        for name, skill_id in raw_selections.items():
            normalized = normalize_product(str(name), self.product_tokens) or str(name).strip().lower()
            selected[normalized] = str(skill_id).strip()
            selected[str(name).strip().lower()] = str(skill_id).strip()
        ctx = {**ctx,
               "platform": normalize_platform(ctx.get("platform")),   # ROB-2: "Kubernetes"/"EKS" -> kubernetes
               "cloud": normalize_cloud(ctx.get("cloud"))}
        include, dead_ends, choice_options, caveats = {}, [], {}, []
        dependencies = set()  # (dependent key, prerequisite key)

        def add_choice(need, options):
            options = set(options)
            if need in choice_options:
                # One detected context must satisfy every dependent capability.
                choice_options[need].intersection_update(options)
            else:
                choice_options[need] = options

        def requested_selection(*names):
            for name in names:
                if name and name.lower() in selected:
                    return selected[name.lower()]
            return None

        def accepts_id(node, skill_id):
            return skill_id == node["id"] or skill_id in self.alias_ids_by_key.get(node["key"], set())

        def choose_implementation(candidates, need, *selection_names):
            """Choose among equally applicable, non-duplicate capabilities."""
            candidates = sorted(candidates, key=lambda n: (n["id"], n["key"]))
            if not candidates:
                return None
            if len(candidates) == 1:
                return candidates[0]
            selection = requested_selection(*selection_names, need)
            if selection:
                matches = [n for n in candidates if accepts_id(n, selection)]
                if len(matches) == 1:
                    return matches[0]
                dead_ends.append(f"selection '{_san(selection)}' is not available for {_san(need)}")
            add_choice(need, (n["id"] for n in candidates))
            return None

        def product_candidate(tok, label):
            cands = self.enable.get(tok, [])
            if not cands:
                dead_ends.append(f"{_san(label)} — no setup skill yet (not automated)")
                return None
            matches = [n for n in cands if fits(n)]
            if not matches:
                plats = sorted({p for n in cands for p in n["platform"]})
                clouds = sorted({c for n in cands for c in n["cloud"]})
                if plats and (not ctx.get("platform") or ctx.get("platform") == "none"):
                    add_choice("platform", plats)
                elif clouds and (not ctx.get("cloud") or ctx.get("cloud") == "none"):
                    add_choice("cloud", clouds)
                else:
                    where = f"platform='{_san(ctx.get('platform'))}'" if plats else f"cloud='{_san(ctx.get('cloud'))}'"
                    covered = f" (covered: {', '.join(plats or clouds)})" if (plats or clouds) else ""
                    pointer = _serverless_pointer(ctx.get("platform")) if plats else ""
                    dead_ends.append(f"{_san(label)} — not automated for {where}{covered}{pointer}")
                return None

            selection = requested_selection(tok, f"{tok} capability")
            if selection:
                selected_matches = [n for n in matches if accepts_id(n, selection)]
                if len(selected_matches) == 1:
                    return selected_matches[0]
                dead_ends.append(f"selection '{_san(selection)}' is not available for {_san(tok)} capability")
                add_choice(f"{tok} capability", (n["id"] for n in matches))
                return None

            best = max(self._facet_rank(n) for n in matches)
            top = [n for n in matches if self._facet_rank(n) == best]
            return choose_implementation(top, f"{tok} capability", tok)

        def add(n):
            if n:
                include[n["key"]] = n
                if n.get("caveat"):
                    caveats.append(f"{n['id']}: {n['caveat']}")

        def fits(n):
            return self._fits(n, ctx)

        def bind(edge):
            # Returns (node|None, ok). ok=False means a HARD prerequisite could not be met,
            # so the dependent must NOT be planned (bind-then-add — CORR-1). Choice points
            # are recorded as a side effect and also return ok=False.
            if "skill" in edge:
                t = self.by_id.get(edge["skill"]); return (t, t is not None)
            if "product" in edge:                                   # CORR-4: product -> product
                t = product_candidate(edge["product"], f"prerequisite '{edge['product']}'")
                return (t, t is not None)
            cat = edge.get("category")
            if cat == "foundation":
                return (self.account, self.account is not None)
            if cat == "platform-install":
                p = ctx.get("platform")
                if not p or p == "none":
                    add_choice("platform", self.install); return (None, False)
                candidates = [n for n in self.install.get(p, []) if fits(n)]
                generic = [n for n in candidates if n["action"] in ("install", "install-agent")]
                n = choose_implementation(
                    generic or candidates,
                    f"platform-install:{p} capability",
                    f"platform-install:{p}",
                )
                if not n:
                    if not candidates:
                        cloud = ctx.get("cloud")
                        suffix = _cloud_suffix(cloud)
                        dead_ends.append(
                            f"Agent install for platform '{_san(p)}'{suffix} is not automated"
                            + _serverless_pointer(p))
                return (n, n is not None)
            if cat == "cloud-connect":
                c = edge.get("cloud") or ctx.get("cloud")
                if not c or c == "none":
                    add_choice("cloud", self.cloud); return (None, False)
                n = choose_implementation(
                    self.cloud.get(c, []),
                    f"cloud-connect:{c} capability",
                    f"cloud-connect:{c}",
                )
                if not n:
                    if c not in self.cloud:
                        dead_ends.append(f"cloud integration for '{_san(c)}' is not automated")
                return (n, n is not None)
            return (None, True)   # unknown edge: not a hard blocker

        def plan_node(n, visiting, visited):
            # (ok, staged, edges): stage n and all satisfiable hard prerequisites.
            # Keep visiting separate from visited so a real dependency cycle is a
            # hard failure rather than being silently treated as a shared node.
            if n["key"] in visiting:
                start = visiting.index(n["key"])
                cycle = visiting[start:] + [n["key"]]
                dead_ends.append("dependency cycle: " + " -> ".join(cycle))
                return (False, {}, set())
            if n["key"] in visited:
                return (True, {}, set())
            visiting.append(n["key"])
            staged, edges, ok = {n["key"]: n}, set(), True
            for r in n["requires"]:
                t, edge_ok = bind(r)
                if not edge_ok:
                    ok = False; continue
                if t:
                    edges.add((n["key"], t["key"]))
                    sub_ok, sub, sub_edges = plan_node(t, visiting, visited)
                    staged.update(sub)
                    edges.update(sub_edges)
                    ok = ok and sub_ok
                    # A product pulled in as a hard prerequisite must complete its
                    # onboarding verification before the dependent runs, just as it
                    # would when requested directly.
                    if "product" in r:
                        for verifier in self.verify.get(r["product"], []):
                            if not fits(verifier):
                                continue
                            edges.add((n["key"], verifier["key"]))
                            verify_ok, verify_nodes, verify_edges = plan_node(
                                verifier, visiting, visited)
                            staged.update(verify_nodes)
                            edges.update(verify_edges)
                            ok = ok and verify_ok
            visiting.pop()
            visited.add(n["key"])
            return (ok, staged, edges)

        def stage(node, label):
            dead_end_count = len(dead_ends)
            choices_before = {need: set(options) for need, options in choice_options.items()}
            ok, staged, edges = plan_node(node, [], set())
            if ok:
                for v in staged.values():
                    add(v)
                dependencies.update(edges)
                return True
            # Preserve a precise bind error or an actionable choice without adding
            # a second, generic dead-end that tells the user nothing new.
            choices_changed = choices_before != choice_options
            if len(dead_ends) == dead_end_count and not choices_changed:
                dead_ends.append(
                    f"{_san(label)} — not set up: a required prerequisite is unavailable for this context")
            return False

        # Process products in a canonical order so the plan is INDEPENDENT of input order:
        # installer-delivered products (infra/logs) go LAST, so they reuse an already-staged
        # specific installer (e.g. apm-agent-install-*) instead of adding a redundant generic
        # agent install. Non-installer products keep a stable token order. (combinatorial invariant)
        def _proc_order(nm):
            t = normalize_product(nm, self.product_tokens)
            return (1 if t in self.installer_products else 0, t or "", str(nm))
        for name in sorted(products, key=_proc_order):
            tok = normalize_product(name, self.product_tokens)
            if tok is None:
                guess = self._nearest_product(name)
                hint = f' — did you mean "{guess[0]}" ({guess[1]})?' if guess else ""
                dead_ends.append(f"unrecognized product '{_san(name)}'{hint}")
                continue
            if tok in self.installer_products:       # delivered by an installer; no separate enable skill
                p = ctx.get("platform")
                if not p or p == "none":
                    add_choice("platform", self.install); continue
                candidates = []
                for candidate in self.install.get(p, []):
                    delivered = candidate.get("delivers", [])
                    if tok in delivered and fits(candidate):
                        candidates.append(candidate)
                already_staged = [n for n in candidates if n["key"] in include]
                generic = [n for n in candidates if n["action"] in ("install", "install-agent")]
                node = choose_implementation(
                    already_staged or generic or candidates,
                    f"platform-install:{p} capability",
                    f"platform-install:{p}",
                )
                if not node:
                    if not candidates:
                        cloud = ctx.get("cloud")
                        suffix = _cloud_suffix(cloud)
                        if not self.install.get(p):
                            dead_ends.append(
                                f"{_san(name)} — Agent install for platform '{_san(p)}' is not automated"
                                + _serverless_pointer(p))
                        elif not any(fits(candidate) for candidate in self.install.get(p, [])):
                            dead_ends.append(
                                f"{_san(name)} — Agent install for platform '{_san(p)}'{suffix} "
                                "is not automated")
                        else:
                            dead_ends.append(
                                f"{_san(name)} — Agent install for platform '{_san(p)}'{suffix} "
                                "does not deliver this product")
                    continue
                # Delivery is catalog data, not resolver policy. A platform installer
                # without an explicit contract cannot be assumed to enable any product.
                delivered_products = node.get("delivers", [])
                if tok not in delivered_products:
                    dead_ends.append(f"{_san(name)} — Agent install for platform '{_san(p)}' does not deliver this product")
                    continue
                if stage(node, name):
                    # Some products have additive, context-specific setup paths. For
                    # example, cloud log forwarding belongs in a Logs plan only when
                    # that cloud was actually detected. Its own hard prerequisites are
                    # staged normally; it is never suggested for unrelated products.
                    for triggered in self.triggered.get(tok, []):
                        if fits(triggered):
                            stage(triggered, f"{name} ({triggered['id']})")
                continue
            m = product_candidate(tok, name)
            if not m:
                continue
            if stage(m, name):                       # bind-then-add: only attach verify if the product itself resolved
                for v in self.verify.get(tok, []):
                    if not fits(v):
                        continue
                    add(v)
                    dependencies.add((v["key"], m["key"]))

        def stable_key(key):
            n = include[key]
            return (KIND_RANK[n["kind"]], n.get("product") or "", n["id"], n["key"])

        # Linearize into a deterministic, sequential plan with dependency-chain
        # LOCALITY: after a skill completes, its newly-unblocked direct dependents
        # are preferred over unrelated nodes that were already ready, so a chain
        # stays contiguous instead of interleaving with a sibling branch. Ties fall
        # back to the stable key (kind, product, id, key). This is a depth-first walk
        # of the hard-edge DAG; it stays independent of product input order because
        # every choice is made by stable_key, not arrival order. `kind` still only
        # orders nodes not already constrained by a hard edge (e.g. AAP->APM).
        pending = {key: set() for key in include}
        dependents = {key: [] for key in include}
        for dependent, prerequisite in dependencies:
            if dependent in pending and prerequisite in pending:
                pending[dependent].add(prerequisite)
                dependents[prerequisite].append(dependent)
        plan = []
        emitted = set()
        # LIFO frontier of ready keys. Push newly-ready keys in REVERSE stable order
        # so the stable-min is popped first; the LIFO discipline drains the most
        # recently unblocked chain before returning to older ready siblings.
        frontier = sorted((k for k, deps in pending.items() if not deps),
                          key=stable_key, reverse=True)
        while frontier:
            key = frontier.pop()
            if key in emitted:
                continue
            plan.append(include[key])
            emitted.add(key)
            newly = []
            for dep in dependents[key]:
                pending[dep].discard(key)
                if not pending[dep] and dep not in emitted:
                    newly.append(dep)
            frontier.extend(sorted(newly, key=stable_key, reverse=True))
        if len(emitted) != len(include):
            cycle = sorted((k for k in include if k not in emitted), key=stable_key)
            dead_ends.append("dependency cycle among planned skills: " + ", ".join(cycle))

        # dedupe by id, preserve order
        seen_ids, ordered, ordered_keys = set(), [], []
        for n in plan:
            if n["id"] in seen_ids:
                continue
            seen_ids.add(n["id"])
            ordered_keys.append(n["key"])
            ordered.append({"id": n["id"], "kind": n["kind"], "product": n["product"],
                            "platform": n["platform"], "cloud": n["cloud"],
                            "url": n["source"]["url"], "source": n["source"]})

        # A compact, human-readable view of the same plan as a DAG. Nodes stay in
        # topological order, and `requires` contains only direct prerequisites.
        position = {n["key"]: i for i, n in enumerate(plan)}
        requires_by_key = {n["key"]: set() for n in plan}
        for dependent, prerequisite in dependencies:
            if dependent in requires_by_key and prerequisite in position:
                requires_by_key[dependent].add(prerequisite)
        dag = [
            {
                "skill": include[key]["id"],
                "requires": [
                    include[required]["id"]
                    for required in sorted(requires_by_key[key], key=position.get)
                ],
            }
            for key in ordered_keys
        ]

        # optional enrichments (suggests) — bound to context, never a dead-end/choice
        plan_ids = {x["id"] for x in ordered}
        suggested = {}
        for n in include.values():
            for s in n.get("suggests", []):
                t = self.by_id.get(s["skill"]) if "skill" in s else self._bind_soft(s.get("category"), ctx, s.get("cloud"))
                if t and t["id"] not in plan_ids and t["key"] not in suggested:
                    suggested[t["key"]] = {"id": t["id"], "kind": t["kind"], "product": t["product"],
                                           "url": t["source"]["url"], "source": t["source"],
                                           "suggested_by": n["id"]}
        return {"plan": ordered,
                "dag": dag,
                "suggested": list(suggested.values()),
                "caveats": list(dict.fromkeys(caveats)),
                "dead_ends": list(dict.fromkeys(dead_ends)),
                "choices": [{"need": need, "options": sorted(options)}
                            for need, options in choice_options.items()]}


def _print(res):
    # Render the plan as an aligned table so a debugger user sees each skill AND its
    # full source URL in a separate column before anything is dispatched (spec req
    # "Skill source URLs in output"). Rows keep the `  N. <id>` shape parsers rely on.
    plan = res["plan"]
    print("PLAN (in order) — skills to execute, with source URLs:")
    if plan:
        rows = [(f"{i}.", p["id"],
                 p["kind"] + (f"/{p['product']}" if p["product"] else ""),
                 p["url"]) for i, p in enumerate(plan, 1)]
        nw = max([len(r[0]) for r in rows] + [1])
        iw = max([len(r[1]) for r in rows] + [len("SKILL")])
        kw = max([len(r[2]) for r in rows] + [len("KIND")])
        print(f"  {'#'.ljust(nw)}  {'SKILL'.ljust(iw)}  {'KIND'.ljust(kw)}  URL")
        for num, sid, kind, url in rows:
            print(f"  {num.ljust(nw)}  {sid.ljust(iw)}  {kind.ljust(kw)}  {url}")
    else:
        print("  (no skills to execute)")
    if res.get("suggested"):
        print("SUGGESTED (optional enrichment for the detected context):")
        for s in res["suggested"]:
            print(f"  + {s['id']}  [{s['kind']}]  (suggested by {s['suggested_by']})  {s['url']}")
    if res.get("caveats"):
        print("CAVEATS (scope limits of a routed skill):")
        for c in res["caveats"]:
            print(f"  ! {c}")
    if res["dead_ends"]:
        print("DEAD-ENDS (recommended, not automated — demand signal):")
        for d in res["dead_ends"]:
            print(f"  - {d}")
    if res["choices"]:
        print("CHOICE POINTS (ask the developer):")
        for c in res["choices"]:
            print(f"  - pick a {c['need']}: {', '.join(c['options'])}")


def _print_trace(session_id, args, res):
    """Emit a stable, machine-readable TRACE block the SKILL.md runbook pastes verbatim
    into the run's trace file (T2.1). The judge grades that trace, so having the deterministic
    planner author it — instead of the model re-narrating the plan — removes a whole class
    of transcription error. Deterministic for fixed inputs; SESSION_ID is the only
    run-seeded field (pin it with DD_ORCH_SESSION_ID). CONFIRMED and DISPATCHED are
    placeholders the agent fills after the confirm gate and after each dispatch.

    STOP_REASON tells the reader whether the plan may proceed:
      none                  — plan is non-empty and no choices remain; go to the confirm gate.
      awaiting_choice       — a CHOICE_POINT is unresolved (e.g. missing platform); resolve it
                              and re-run first, even if a partial plan already exists.
      no_enabled_capability — no plan and no choice; only dead-ends (nothing to automate).
      no_plan               — nothing to do (empty product list / all filtered out).
    """
    plan = res["plan"]
    if res["choices"]:          # an unresolved choice blocks dispatch, even with a partial plan
        stop = "awaiting_choice"
    elif plan:
        stop = "none"
    elif res["dead_ends"]:
        stop = "no_enabled_capability"
    else:
        stop = "no_plan"
    plat = normalize_platform(args.platform) or "none"
    cl = normalize_cloud(args.cloud) or "none"
    print("=== DD-ORCH TRACE v1 ===")
    print(f"SESSION_ID: {session_id}")
    print(f"CONTEXT: platform={plat} cloud={cl}")
    print(f"STOP_REASON: {stop}")
    if plan:
        print("PLAN:")
        for i, p in enumerate(plan, 1):
            print(f"  {i} {p['id']} {p['kind']} {p['product'] or '-'} {p['url']}")
    else:
        print("PLAN: (empty)")
    if res["dead_ends"]:
        print("DEAD_ENDS:")
        for d in res["dead_ends"]:
            print(f"  - {d}")
    else:
        print("DEAD_ENDS: (none)")
    if res["choices"]:
        print("CHOICE_POINTS:")
        for c in res["choices"]:
            print(f"  - {c['need']}: {', '.join(c['options'])}")
    else:
        print("CHOICE_POINTS: (none)")
    if res.get("suggested"):
        print("SUGGESTED:")                                     # optional enrichment, e.g. a cloud connector
        for s in res["suggested"]:
            print(f"  + {s['id']} {s['kind']} (by {s['suggested_by']}) {s['url']}")
    else:
        print("SUGGESTED: (none)")
    print("CONFIRMED: pending")
    print("DISPATCHED: (fill one skill_id per line after each dispatch)")
    print("=== END DD-ORCH TRACE ===")


def _print_dag(res):
    """Render the plan as an ASCII DAG (debug mode): indentation encodes dependency
    depth so locality-ordered chains read as staircases, and `<-` lists each skill's
    direct prerequisites so multi-parent edges stay explicit."""
    dag = res["dag"]
    if not dag:
        print("DAG: (empty — no skills to execute)")
        return
    order = [n["skill"] for n in dag]
    reqs = {n["skill"]: n["requires"] for n in dag}
    depth = {}
    for skill in order:  # plan order guarantees prerequisites precede dependents
        rs = reqs[skill]
        depth[skill] = 1 + max(depth[r] for r in rs) if rs else 0
    print("DAG (skills to execute - indent = dependency depth, `<-` = direct prerequisites):")
    for skill in order:
        edge = f"  <- {', '.join(reqs[skill])}" if reqs[skill] else ""
        print(f"{'  ' * (depth[skill] + 1)}{skill}{edge}")


def _emit_telemetry(session_id, args, products, router, res):
    """Emit resolve.py's three process-guaranteed events, best-effort (v1).

    This is the RELIABLE CORE of the orchestrator's telemetry — the SKILL.md runbook
    drives the per-dispatch skill_step events, which a markdown runbook cannot make
    process-grade. It never affects the resolve result or the exit path: emit.py is
    best-effort and the whole body is guarded, so any failure is swallowed.
    """
    try:
        import emit

        base = {
            "invocation_mode": "orchestrated",
            "entry_skill_id": "dd-orchestrator",
            "agent_name": getattr(args, "agent_name", None) or "unknown",
            "intent_mode": getattr(args, "intent_mode", None),   # explicit (shortcut) | recommended
            "target_platform": normalize_platform(args.platform),
            "target_cloud": normalize_cloud(args.cloud),
            "org_id": os.environ.get("DD_ORG_ID") or None,   # auth'd org public_id; None -> omitted
        }
        # Persist the run envelope once so the SKILL.md runbook's later emit.py processes
        # re-attach it to every started/finished/skill_run:finished event (F4).
        emit.write_session_state(session_id, base, shape={
            "dead_end_count": len(res["dead_ends"]),
            "planned_skill_count": len(res["plan"]),
            "choice_count": len(res["choices"]),
        })
        emit.emit("skill_run", "started", session_id, dict(base), critical=True)

        tokens = sorted({t for t in (normalize_product(p, router.product_tokens)
                                     for p in products) if t})
        dependency_count = {node["skill"]: len(node["requires"]) for node in res["dag"]}
        # F2: map each step to its direct prerequisites' plan positions so the DAG edges
        # (not just a count) are reconstructable from logs.
        position_of = {node["id"]: i for i, node in enumerate(res["plan"], 1)}
        requires_of = {node["skill"]: node["requires"] for node in res["dag"]}
        emit.emit("skill_run", "plan_resolved", session_id, {
            **base,
            "recommended_products": ",".join(tokens),
            "planned_skill_count": len(res["plan"]),
            "dead_end_count": len(res["dead_ends"]),
            "choice_count": len(res["choices"]),
        }, critical=True)

        for position, node in enumerate(res["plan"], 1):
            deps = sorted(position_of[r] for r in requires_of.get(node["id"], [])
                          if r in position_of)
            step = {
                **base,
                "step_kind": "skill",
                "plan_position": position,
                "skill_id": node["id"],
                "skill_kind": node["kind"],
                "product": node.get("product") or "",
                "source_repo": (node.get("source") or {}).get("repo") or "",
                "dependency_count": dependency_count.get(node["id"], 0),
                "skill_invoked": False,
                "instrumentation_invoked": False,
            }
            if deps:                                     # omit for root steps (no edges)
                step["depends_on"] = ",".join(str(p) for p in deps)
            emit.emit("skill_step", "planned", session_id, step, critical=True)

        # ponytail: one coverage_gap per dead-end, counted only. resolve.py's dead_ends are
        # free text and privacy (proposal §8) forbids echoing them; splitting them into
        # per-product tokens is a follow-up. dead_end_count (above) already carries the total.
        for _dead_end in res["dead_ends"]:
            emit.emit("skill_step", "planned", session_id, {
                **base,
                "step_kind": "coverage_gap",
                "skill_invoked": False,
                "instrumentation_invoked": False,
                "result": "not_automated",
            }, critical=True)
    except Exception:
        return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--products", default="")
    ap.add_argument("--platform", default="none")
    ap.add_argument("--cloud", default="none")
    ap.add_argument(
        "--select",
        action="append",
        default=[],
        metavar="PRODUCT=SKILL_ID",
        help="resolve an implementation choice (repeatable)",
    )
    ap.add_argument(
        "--include-disabled",
        action="store_true",
        help="route over the full capability model, including disabled (private / not-GA) "
             "implementations; shows the path a skill would take once its source is enabled",
    )
    ap.add_argument(
        "--debug",
        action="store_true",
        help="also render the ASCII DAG of the skills to be executed",
    )
    ap.add_argument(
        "--trace",
        action="store_true",
        help="emit a stable, machine-readable TRACE block (STOP_REASON, PLAN, DEAD_ENDS, "
             "CHOICE_POINTS, CONFIRMED, DISPATCHED) for the run's trace file instead of the human table",
    )
    ap.add_argument(
        "--list-products",
        action="store_true",
        help="print the accepted product vocabulary (recommender names + catalog tokens) and exit",
    )
    ap.add_argument(
        "--detect-products",
        default=None,
        metavar="INTENT",
        help="scan a free-text intent for explicitly named products; print the matched product "
             "tokens (one CSV line, empty if none) and exit. Empty output => run the recommender",
    )
    ap.add_argument(
        "--agent-name",
        default="unknown",
        help="executor agent for telemetry: claude_code | codex | cursor | unknown",
    )
    ap.add_argument(
        "--intent-mode",
        choices=("explicit", "recommended"),
        default=None,
        help="product provenance (REQUIRED with --trace): 'explicit' if the intent named the products "
             "(--detect-products shortcut), 'recommended' if dd-product-recommender produced them. The "
             "orchestrator may not compose a plan from products it inferred itself.",
    )
    a = ap.parse_args()
    selections = {}
    for item in a.select:
        if "=" not in item:
            ap.error(f"--select must be PRODUCT=SKILL_ID, got {item!r}")
        product, skill_id = item.split("=", 1)
        if not product.strip() or not skill_id.strip():
            ap.error(f"--select must be PRODUCT=SKILL_ID, got {item!r}")
        selections[product.strip()] = skill_id.strip()
    catalog = load_catalog()
    if a.list_products:
        router = Router(catalog, enabled_only=not a.include_disabled)
        print("Accepted --products inputs (case-insensitive, whitespace-tolerant):")
        print("  names/aliases: " + ", ".join(sorted(PRODUCT_TOKENS)))
        print("  catalog tokens: " + ", ".join(sorted(router.product_tokens)))
        return 0
    if a.detect_products is not None:
        # Shortcut: the intent may already name products. If so, skip the recommender and
        # route them directly; empty output tells the runbook to recommend instead.
        router = Router(catalog, enabled_only=not a.include_disabled)
        print(",".join(detect_products(a.detect_products, router.product_tokens)))
        return 0
    # Gate: a composed trace must declare product provenance — the --detect-products shortcut or
    # dd-product-recommender. The orchestrator may not build a plan from products it inferred itself.
    if a.trace and a.products.strip() and not a.intent_mode:
        ap.error("--trace with --products requires --intent-mode explicit|recommended: products must "
                 "come from the --detect-products shortcut or dd-product-recommender, never inferred "
                 "by the orchestrator")
    # One session id per invocation, shared by every event in this DAG run. Minted here
    # (or taken from the environment if an outer wrapper already set it) and printed so the
    # SKILL.md runbook can reuse it on each dispatch-boundary emit.py call.
    session_id = os.environ.get("DD_ORCH_SESSION_ID") or str(uuid.uuid4())
    if not a.trace:
        # In --trace mode the framed block already carries SESSION_ID; keep this human-oriented
        # line for plain runs only, so the trace stays a single machine-readable block.
        print(f"SESSION ID: {session_id}")
    products = [p for p in a.products.split(",") if p.strip()]
    router = Router(catalog, enabled_only=not a.include_disabled)
    res = router.resolve(
        products,
        {"platform": a.platform, "cloud": a.cloud},
        selections=selections,
    )
    if a.trace:
        _print_trace(session_id, a, res)
    else:
        _print(res)
    if a.debug:
        _print_dag(res)
    _emit_telemetry(session_id, a, products, router, res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
