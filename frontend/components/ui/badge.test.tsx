import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Badge, ProviderBadge, SeverityBadge, StatusBadge, VerificationBadge } from "@/components/ui/badge";

afterEach(() => {
  cleanup();
});

describe("badge components", () => {
  it("renders the explicit NOT RUN label for unexecuted datasets", () => {
    render(<StatusBadge status="not_run" />);
    expect(screen.getByText("NOT RUN")).toBeInTheDocument();
  });

  it("never labels AI output as verified", () => {
    render(<VerificationBadge status="ai_suggested" />);
    expect(screen.getByText("AI suggested")).toBeInTheDocument();
    expect(screen.queryByText("Verified")).not.toBeInTheDocument();
  });

  it("uses danger styling for critical and high severities", () => {
    const { container } = render(<SeverityBadge severity="critical" />);
    expect(screen.getByText("Critical")).toBeInTheDocument();
    expect(container.firstElementChild?.className).toContain("text-danger-strong");
  });

  it("marks the deterministic provider as a mock", () => {
    render(<ProviderBadge provider="openai" isMock />);
    expect(screen.getByText("Mock · openai")).toBeInTheDocument();
    expect(screen.getByTitle(/no model was called/i)).toBeInTheDocument();
  });

  it("does not mark a real provider as mock", () => {
    render(<ProviderBadge provider="anthropic" />);
    expect(screen.getByText("anthropic")).toBeInTheDocument();
    expect(screen.queryByText(/mock/i)).not.toBeInTheDocument();
  });

  it("supports arbitrary children and tone classes", () => {
    const { container } = render(<Badge tone="success">Ready</Badge>);
    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(container.firstElementChild?.className).toContain("text-success-strong");
  });
});
