---
name: expand-and-structure
description: Use this skill to take raw brain dumps or notes and convert them into a structured Project Specification with executive summary, implementation plan, and open questions. Optionally outputs in spec-driven format for complex projects.
---

# EXPAND & STRUCTURE

**Role:** Senior Systems Architect.
**Context:** I have written a rough brain dump in this file.
**Goal:** Convert this raw thought into a structured Project Specification.

**Action Plan:**
1. **Analyze:** Read the referenced file or specified text. Identify the core objective, constraints, and "unknowns."
2. **Structure:** Refactor the text into a clean Markdown document with these sections:
   - **Executive Summary:** One sentence goal.
   - **The "Why":** Linking to my long-term goals (Career/AI/Life).
   - **Implementation Plan:** Step-by-step phases.
   - **Open Questions:** Things we need to figure out (Socratic method).
3. **Refine:** If the idea implies code (e.g., a new Python script), draft a high-level pseudocode block.

**Constraint:** Do not delete my original text yet; move it to a "## Raw Notes" section at the bottom.

## Spec-Driven Output (Optional)

For complex, multi-phase projects, output in **spec-driven format** instead of a single document. Use this when the brain dump describes something that will need formal requirements, design review, and phased execution.

**How to trigger:** User says "use spec format", "make it a spec", or the idea clearly warrants it (multiple phases, external dependencies, weeks of work).

**Output:** Three files based on templates at `.claude/templates/specs/`:
1. `requirements.md` — What and why (from the brain dump's goals and constraints)
2. `design.md` — How (from any technical details, architecture hints, or decisions in the brain dump)
3. `tasks.md` — Execution plan (from the implementation steps)

Pre-fill as much as possible from the brain dump. Mark "Draft" status on all three. Move original text to the `## Raw Notes` section in requirements.md.

If the project already has a folder in `10_Active_Projects/`, place specs in `{project}/specs/`. Otherwise, create the structured document in place and suggest creating a project folder if the scope warrants it.
