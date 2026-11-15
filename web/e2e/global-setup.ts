import { seed } from "./fixtures";

export default async function globalSetup() {
  await seed();
}
