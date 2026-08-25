import { db, ensureSchema } from "./db.js";
await ensureSchema();
await db.destroy();
console.log("Node database schema is compatible");
