import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, vi, expect, it } from "vitest";
import { ProfileForm } from "./App";

const request = vi.fn(async () => ({}));
vi.mock("./auth", () => ({ useAuth: () => ({ api: { request } }), ApiError: class ApiError extends Error {} }));
afterEach(() => { cleanup(); request.mockReset(); request.mockResolvedValue({}); });

it("populates an existing profile and preserves untouched fields on patch", async () => {
  render(<ProfileForm profile={{ id: "p", user_id: "u", created_at: "", updated_at: "", headline: "Engineer", location: "London" }} onSaved={() => {}} onFeedbackClear={() => {}} />);
  expect(screen.getByRole("textbox", { name: "Headline" })).toHaveValue("Engineer");
  fireEvent.change(screen.getByRole("textbox", { name: "Location" }), { target: { value: "Oxford" } });
  fireEvent.click(screen.getByRole("button", { name: "Save profile" }));
  await vi.waitFor(() => expect(request).toHaveBeenCalled());
  const call = request.mock.calls[0] as unknown as [string, { method: string; body: string }];
  const payload = JSON.parse(call[1].body);
  expect(payload.headline).toBe("Engineer");
  expect(payload.location).toBe("Oxford");
  expect(call[1].method).toBe("PATCH");
});

it("uses multi-line controls for the long-form optional profile fields", () => {
  render(<ProfileForm profile={null} onSaved={() => {}} onFeedbackClear={() => {}} />);
  expect(screen.getByRole("textbox", { name: "Summary" }).tagName).toBe("TEXTAREA");
  expect(screen.getByRole("textbox", { name: "Career goal" }).tagName).toBe("TEXTAREA");
  expect(screen.getByRole("textbox", { name: "Job-search criteria" }).tagName).toBe("TEXTAREA");
});

it("uses create semantics when no profile exists", async () => {
  request.mockClear(); render(<ProfileForm profile={null} onSaved={() => {}} onFeedbackClear={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Save profile" }));
  await vi.waitFor(() => expect(request).toHaveBeenCalled());
  const call = request.mock.calls[0] as unknown as [string, { method: string }];
  expect(call[1].method).toBe("POST");
});

it("shows pending feedback, blocks duplicate profile submits, and calls success only after save resolves", async () => {
  let resolveSave!: (value: {}) => void;
  request.mockReturnValueOnce(new Promise<{}>((resolve) => { resolveSave = resolve; }));
  const onSaved = vi.fn();
  render(<ProfileForm profile={null} onSaved={onSaved} onFeedbackClear={() => {}} />);

  const save = screen.getByRole("button", { name: "Save profile" });
  fireEvent.submit(save.closest("form")!);
  expect(await screen.findByRole("button", { name: "Saving…" })).toBeDisabled();
  expect(screen.getByRole("status")).toHaveTextContent("Saving profile…");
  expect(onSaved).not.toHaveBeenCalled();
  fireEvent.submit(save.closest("form")!);
  expect(request).toHaveBeenCalledTimes(1);

  await act(async () => { resolveSave({}); });
  expect(onSaved).toHaveBeenCalledTimes(1);
});

it("preserves profile values on failure and permits an explicit retry", async () => {
  request.mockRejectedValueOnce(new Error("offline"));
  const onSaved = vi.fn();
  render(<ProfileForm profile={null} onSaved={onSaved} onFeedbackClear={() => {}} />);
  const headline = screen.getByRole("textbox", { name: "Headline" });
  fireEvent.change(headline, { target: { value: "Synthetic profile headline" } });
  fireEvent.submit(screen.getByRole("button", { name: "Save profile" }).closest("form")!);

  expect(await screen.findByRole("alert")).toHaveTextContent("Profile could not be saved.");
  expect(headline).toHaveValue("Synthetic profile headline");
  expect(screen.getByRole("button", { name: "Save profile" })).toBeEnabled();
  expect(onSaved).not.toHaveBeenCalled();

  fireEvent.submit(screen.getByRole("button", { name: "Save profile" }).closest("form")!);
  await vi.waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
});
