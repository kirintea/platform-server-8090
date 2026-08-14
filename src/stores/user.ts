import { defineStore } from "pinia";

interface UserState {
  uid: string | null;
  displayName: string | null;
  email: string | null;
  photoURL: string | null;
}

export const useUserStore = defineStore("user", {
  state: (): UserState => ({
    uid: null,
    displayName: null,
    email: null,
    photoURL: null,
  }),
  actions: {
    setUser(user: { uid: string; displayName: string | null; email: string | null; photoURL: string | null }) {
      this.uid = user.uid;
      this.displayName = user.displayName;
      this.email = user.email;
      this.photoURL = user.photoURL;
    },
    clearUser() {
      this.uid = null;
      this.displayName = null;
      this.email = null;
      this.photoURL = null;
    },
  },
});
