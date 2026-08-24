import { db, migrate } from "./db.js";
await migrate();
await db.destroy();
console.log("Node database migrated");
