export const state = {
  me: null,
  unread: 0,
  currentProject: null,
};

export function setMe(me) { state.me = me; }
export function setUnread(n) { state.unread = n; }

export function isAdmin() { return !!state.me && state.me.role === "ADMIN"; }
export function canManageProjects() {
  return !!state.me && ["ADMIN", "PROJECT_MANAGER"].includes(state.me.role);
}