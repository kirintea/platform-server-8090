export interface Supporter {
  id: string;
  name: string;
  amount: number;
  message: string;
  time: string;
}

export interface Creator {
  id: string;
  name: string;
  photoURL: string;
  email: string;
  bio: string;
  supporters: Supporter[];
  totalEarnings: number;
}
