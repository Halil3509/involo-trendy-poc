import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { User } from "@/lib/types";

import { LabShell } from "@/components/lab-shell";

const mockReplace = vi.fn();
const mockRefresh = vi.fn();

vi.mock("next/navigation", () => ({
  usePathname: () => "/lab",
  useRouter: () => ({ replace: mockReplace, refresh: mockRefresh }),
}));

const mockSignOut = vi.fn();

vi.mock("@/components/auth-provider", () => ({
  useAuth: () => ({
    user: {
      id: "user-1",
      email: "user@example.com",
      role: "user",
      created_at: "2026-07-01T00:00:00Z",
    } as User,
    loading: false,
    refreshUser: vi.fn(),
    signOut: mockSignOut,
  }),
}));

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

describe("LabShell", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the Invo Lab brand and sidebar navigation", () => {
    render(
      <LabShell>
        <div data-testid="content">Lab content</div>
      </LabShell>,
    );

    expect(screen.getByText("Invo Lab")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Overview" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Creators" })).toBeInTheDocument();
    expect(screen.getByText("user@example.com")).toBeInTheDocument();
    expect(screen.getByText("Lab content")).toBeInTheDocument();
  });

  it("does not show brand analysis link for non-admin users", () => {
    render(
      <LabShell>
        <div>Content</div>
      </LabShell>,
    );

    expect(screen.queryByRole("link", { name: "Brand analysis" })).not.toBeInTheDocument();
  });

  it("calls signOut when the sign out button is clicked", async () => {
    const user = userEvent.setup();
    render(
      <LabShell>
        <div>Content</div>
      </LabShell>,
    );

    await user.click(screen.getByRole("button", { name: "Sign out" }));
    expect(mockSignOut).toHaveBeenCalled();
  });
});
