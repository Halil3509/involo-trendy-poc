import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AuthForm } from "@/components/auth-form";
import { api } from "@/lib/api";

const replace = vi.fn();
const refresh = vi.fn();
const refreshUser = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, refresh }),
}));

vi.mock("@/components/auth-provider", () => ({
  useAuth: () => ({ refreshUser }),
}));

vi.mock("@/lib/api", () => ({
  api: {
    login: vi.fn(),
    register: vi.fn(),
  },
}));

describe("AuthForm", () => {
  beforeEach(() => {
    replace.mockReset();
    refresh.mockReset();
    refreshUser.mockReset();
    vi.mocked(api.login).mockReset();
    vi.mocked(api.register).mockReset();
  });

  it("shows accessible validation errors", async () => {
    const user = userEvent.setup();
    render(<AuthForm mode="register" />);

    await user.type(screen.getByLabelText("Email address"), "invalid");
    await user.type(screen.getByLabelText("Password"), "short");
    await user.type(screen.getByLabelText("Confirm password"), "other");
    await user.click(screen.getByRole("button", { name: "Create account" }));

    expect(screen.getByText("Enter a valid email address.")).toBeInTheDocument();
    expect(screen.getByText("Password must be at least 10 characters.")).toBeInTheDocument();
    expect(screen.getByText("Passwords do not match.")).toBeInTheDocument();
    expect(api.register).not.toHaveBeenCalled();
  });

  it("logs in, reloads the profile, and redirects", async () => {
    vi.mocked(api.login).mockResolvedValue({
      id: "user-1",
      email: "user@example.com",
      role: "user",
      created_at: "2026-07-17T18:00:00Z",
    });
    refreshUser.mockResolvedValue({
      id: "user-1",
      email: "user@example.com",
      role: "user",
      created_at: "2026-07-17T18:00:00Z",
    });
    const user = userEvent.setup();
    render(<AuthForm mode="login" />);

    await user.type(screen.getByLabelText("Email address"), "user@example.com");
    await user.type(screen.getByLabelText("Password"), "password123");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(api.login).toHaveBeenCalledWith("user@example.com", "password123");
    expect(refreshUser).toHaveBeenCalled();
    expect(replace).toHaveBeenCalledWith("/dashboard");
  });
});
