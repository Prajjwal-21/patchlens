import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FeedbackForm } from "@/components/finding-feedback";
import { ApiError, postJson } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, postJson: vi.fn() };
});

const mockedPostJson = vi.mocked(postJson);

afterEach(() => {
  cleanup();
});

beforeEach(() => {
  mockedPostJson.mockReset();
});

describe("FeedbackForm", () => {
  it("posts a human label with a trimmed optional comment", async () => {
    mockedPostJson.mockResolvedValue({
      id: "feedback-1",
      finding_id: "finding-1",
      label: "incorrect",
      comment: "Does not reproduce",
      reviewer: "dashboard",
      source: "human_review",
      created_at: "2026-01-01T00:00:00Z",
    });
    const onSubmitted = vi.fn();

    render(<FeedbackForm findingId="finding-1" onSubmitted={onSubmitted} />);

    fireEvent.change(screen.getByLabelText("Feedback label"), {
      target: { value: "incorrect" },
    });
    fireEvent.change(screen.getByLabelText("Comment (optional)"), {
      target: { value: "  Does not reproduce  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Record" }));

    await waitFor(() =>
      expect(mockedPostJson).toHaveBeenCalledWith("/api/v1/findings/finding-1/feedback", {
        label: "incorrect",
        comment: "Does not reproduce",
        reviewer: "dashboard",
        source: "human_review",
      }),
    );
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Feedback recorded. Labels are stored for evaluation",
    );
    expect(onSubmitted).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText("Comment (optional)")).toHaveValue("");
  });

  it("sends null for an empty comment", async () => {
    mockedPostJson.mockResolvedValue({});
    render(<FeedbackForm findingId="finding with space" />);

    fireEvent.click(screen.getByRole("button", { name: "Record" }));

    await waitFor(() =>
      expect(mockedPostJson).toHaveBeenCalledWith(
        "/api/v1/findings/finding%20with%20space/feedback",
        expect.objectContaining({ label: "correct", comment: null }),
      ),
    );
  });

  it("surfaces API errors and does not claim success", async () => {
    mockedPostJson.mockRejectedValue(
      new ApiError("Feedback is disabled for this finding", {
        status: 409,
        code: "feedback_disabled",
      }),
    );
    const onSubmitted = vi.fn();

    render(<FeedbackForm findingId="finding-1" onSubmitted={onSubmitted} />);
    fireEvent.click(screen.getByRole("button", { name: "Record" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Feedback is disabled for this finding",
    );
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(onSubmitted).not.toHaveBeenCalled();
  });
});
