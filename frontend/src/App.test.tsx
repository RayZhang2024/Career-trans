import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, vi, expect, it } from "vitest";
import { ProfileForm } from "./App";

const request = vi.fn(async () => ({}));
vi.mock("./auth", () => ({ useAuth: () => ({ api: { request } }), ApiError: class ApiError extends Error {} }));
afterEach(cleanup);

it("populates an existing profile and preserves untouched fields on patch", async () => {
  render(<ProfileForm profile={{ id: "p", user_id: "u", created_at: "", updated_at: "", headline: "Engineer", location: "London" }} onSaved={() => {}} />);
  expect(screen.getByRole("textbox", { name: "headline" })).toHaveValue("Engineer");
  fireEvent.change(screen.getByRole("textbox", { name: "location" }), { target: { value: "Oxford" } });
  fireEvent.click(screen.getByRole("button", { name: "Save profile" }));
  await vi.waitFor(() => expect(request).toHaveBeenCalled());
  const call = request.mock.calls[0] as unknown as [string, { method: string; body: string }];
  const payload = JSON.parse(call[1].body);
  expect(payload.headline).toBe("Engineer");
  expect(payload.location).toBe("Oxford");
  expect(call[1].method).toBe("PATCH");
});

it("uses create semantics when no profile exists", async () => {
  request.mockClear(); render(<ProfileForm profile={null} onSaved={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Save profile" }));
  await vi.waitFor(() => expect(request).toHaveBeenCalled());
  const call = request.mock.calls[0] as unknown as [string, { method: string }];
  expect(call[1].method).toBe("POST");
});
